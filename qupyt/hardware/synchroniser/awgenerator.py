import logging
import pickle
from collections.abc import Sequence
from pathlib import Path
from time import time
from typing import Any, cast

import numpy as np
import yaml
from tqdm import tqdm

from qupyt.hardware.visa_handler import VisaObject
from qupyt.pulse_sequences.sequence_designer import (
    PulseSequenceYaml,
    parse_awg_flag,
    validate_sequence_instructions,
)
from qupyt.set_up import get_seq_dir
from qupyt.utils.errors import cleanup_after_error, cleanup_on_exit

from ._awg_common import (
    DEFAULT_MAX_SEQUENCE_STEPS,
    DEFAULT_MAX_STEP_REPEAT,
    AWGSequenceStep,
    build_logical_awg_steps,
    get_awg_sequence_options,
    infer_child_repetitions,
    validate_awg_limit,
    validate_awg_metadata_lengths,
    validate_awg_repeat_count,
    validate_repeated_child_region,
)
from .base import Synchroniser

logger = logging.getLogger(__name__)


class AWGenerator(VisaObject, Synchroniser, synchroniser_types=("TekAWG",)):
    """Synchroniser implementation for Tektronix AWGs.

    Flat mode preserves the existing Tek AWG behavior. repeated_child mode uses
    Tek AWG70000-series sequence-asset SCPI and was tested on AWG70001B.

    Parameters
    ----------
    configuration : dict | None
        Configuration keys:
        - ``address`` (str, required): VISA address, e.g.
        ``"TCPIP::<ip>::INSTR"``. The connection is opened in ``__init__``.
        - ``sampling_rate`` (float): samples per second, default ``5e9``.
        - ``channels`` (list[int]): AWG source channels to use, default
        ``[1, 2]`` (reduced to the outputs the instrument has on
        :meth:`open`).
        - ``device_type`` (str): name used in log messages, default ``"TekAWG"``.
        - ``awg_max_sequence_steps`` (int, default 16384) and
        ``awg_max_step_repeat`` (int, default 1048576): sequencer limits.
    channel_mapping : dict[str, int | str]
        Channel name → either a row of the waveform
        (int: ``5 * i`` analog, ``5 * i + 1`` to ``5 * i + 4`` markers of
        source ``i``, see :meth:`PulseSequence.add_pulse
        <qupyt.pulse_sequences.sequence_designer.PulseSequence.add_pulse>`)
        or a sequence flag selector (str: ``"A"``-``"D"``, optionally with
        the source, e.g. ``"A2"``).
    """

    def __init__(
        self,
        configuration: dict[str, Any] | None,
        channel_mapping: dict[str, int | str],
    ) -> None:
        self.device_type: str = "TekAWG"
        self.samprate: float = 5e9
        self.channel_mapping = channel_mapping
        self.flag_channels = self._extract_flags(channel_mapping)

        self.wavenames: list[str]
        self.seqrepeats: list[int]
        self.waveform_block: np.ndarray
        self.flag_values: dict[str, list[str]]  # set by _load_sequence_block
        self.sequence_options: dict[str, Any] = {}
        self.analog_amplitude: float = 1.0
        self.marker_amplitude: float = 1.75
        self.dac_resolution: int = 12
        self.channels: list[int] = [1, 2]
        self.marker_channels: list[int] = [1, 2, 3, 4]
        self.awg_max_sequence_steps: int = DEFAULT_MAX_SEQUENCE_STEPS
        self.awg_max_step_repeat: int = DEFAULT_MAX_STEP_REPEAT

        Synchroniser.__init__(self, configuration, channel_mapping)
        self.attribute_map["device_type"] = self._set_device_type
        self.attribute_map["sampling_rate"] = self._set_sampling_rate_attribute
        self.attribute_map["channels"] = self._set_channels_attribute
        self.attribute_map["awg_max_sequence_steps"] = self._set_awg_max_sequence_steps
        self.attribute_map["awg_max_step_repeat"] = self._set_awg_max_step_repeat
        if configuration is not None:
            self._update_from_configuration(configuration)
        VisaObject.__init__(self, self.address, self.device_type)
        try:
            self.instance.timeout = 20000
            self._limit_channels_to_available_outputs()
        except BaseException:
            logger.exception("AWG initialization failed at %s", self.address)
            # Close the connection opened above before re-raising.
            cleanup_after_error(
                lambda: VisaObject.close(self), "AWG initialization cleanup"
            )
            raise

    def _extract_flags(self, channel_mapping: dict[str, int | str]) -> list[str]:
        selectors = []
        for value in channel_mapping.values():
            if isinstance(value, int):
                continue
            # Validate syntax here; active channels are configured afterwards.
            parse_awg_flag(value, [1, 2])
            if value not in selectors:
                selectors.append(value)
        return selectors

    def _validate_flag_channels(self) -> None:
        for selector in self.flag_channels:
            parse_awg_flag(selector, self.channels)

    def open(self) -> None:
        self._limit_channels_to_available_outputs()
        self._configure()

    def close(self) -> None:
        """Do nothing (the VISA connection is kept open).

        The VISA connection to the AWG is not closed here (unchanged from the
        original implementation).
        """

    def run(self) -> None:
        self.instance.write("awgcontrol:run:immediate")
        logger.info("Turned on AWG output to RUN immediate".ljust(65, ".") + "[done]")
        self.opc_wait()

    def stop(self) -> None:
        self.instance.write("awgcontrol:stop:immediate")
        logger.info("AWG output stopped".ljust(65, ".") + "[done]")
        self.opc_wait()

    def trigger(self) -> None:
        self.instance.write("trigger:immediate atrigger")
        logger.info("Sent trigger to AWG".ljust(65, ".") + "[done]")

    def load_sequence(self, ps_yaml_file: Path) -> None:
        self._limit_channels_to_available_outputs()
        self._validate_flag_channels()
        self.stop()
        self.instance.write("*CLS")
        self._clear_awg()
        self.sequence_options = self._load_awg_sequence_options(ps_yaml_file)
        sequence_translator = PulseSequenceYaml(
            self.channel_mapping,
            self.channels,
            samprate=self.samprate,
            yaml_file=ps_yaml_file,
        )
        sequence_translator.translate_yaml_to_numeric_instructions()
        self._load_sequence_block(get_seq_dir() / "sequence.npz")
        self._get_logical_awg_steps()  # Validate cached flag metadata before upload.
        self._upload_waveforms()
        self._raise_on_awg_errors("waveform upload")
        self._sequence("autoseq", nongatereps=1)
        self._raise_on_awg_errors("sequencer construction")
        logger.info(
            "Loaded and sequenced current pulse sequence".ljust(65, ".") + "[done]"
        )
        self.opc_wait()

    def _configure(self) -> None:
        self._set_sampling_rate()
        for channel in self.channels:
            self._set_daq_resolution(channel, self.dac_resolution)
            self._set_analog_amplitude(channel, self.analog_amplitude)
            self._set_output_on(channel)
            for marker in self.marker_channels:
                self._set_marker_amplitude(channel, marker, self.marker_amplitude)
        logger.info("Configuring AWG".ljust(65, ".") + "[done]")

    def _upload_waveform(self, wavename: str, waveform: np.ndarray) -> None:
        self.instance.write('wlist:waveform:delete "' + wavename + '"')
        self.instance.write(
            'wlist:waveform:new "' + wavename + '",' + str(waveform.shape[1]) + ",real"
        )
        self.instance.write_binary_values(
            # pyvisa types this as Sequence; numpy arrays work and are faster.
            'wlist:waveform:data "' + wavename + '",',
            cast(Sequence[Any], waveform[0, :]),
        )
        self.instance.write_binary_values(
            'wlist:waveform:marker:data "' + wavename + '",',
            cast(Sequence[Any], waveform.astype(np.uint8)[1, :]),
            datatype="B",
        )
        logger.info(f"Uploaded waveform {wavename}".ljust(65, ".") + "[done]")
        self.opc_wait()

    def _sequence(self, seqname: str, nongatereps: int = 1) -> None:
        mode = self._get_awg_sequence_mode()
        logical_steps = self._get_logical_awg_steps()
        if mode == "flat":
            self._sequence_flat(seqname, logical_steps, nongatereps=nongatereps)
            return
        if mode == "repeated_child":
            self._raise_if_repeated_child_is_unverified_model()
            self._sequence_repeated_child(
                seqname, logical_steps, nongatereps=nongatereps
            )
            return
        raise ValueError(
            f"Unsupported awg_sequence_mode: {mode!r}. Supported values are "
            "'flat' and 'repeated_child'."
        )

    def _sequence_flat(
        self, seqname: str, logical_steps: list[AWGSequenceStep], nongatereps: int = 1
    ) -> None:
        if len(logical_steps) > self.awg_max_sequence_steps:
            raise ValueError(
                f"Expanded AWG sequence has {len(logical_steps)} steps, exceeding "
                f"the configured AWG limit of {self.awg_max_sequence_steps}. Enable "
                "awg_sequence_mode: repeated_child or reduce the sequence."
            )
        logger.info("Tek AWG sequence mode: flat; logical_steps=%s", len(logical_steps))
        # AWG70K sequence assets have one or more tracks. Each active AWG output
        # is assigned one track of the same sequence asset via SOURCE:CASS.
        tracks = len(self.channels)
        self.instance.write('slist:sequence:delete "sub"')
        self.instance.write(f'slist:sequence:new "sub",{len(logical_steps)},{tracks}')
        self.instance.write('slist:sequence:event:jtiming "sub", immediate')
        for i, step in enumerate(logical_steps, start=1):
            for track, channel in enumerate(self.channels, start=1):
                self._configure_waveform_step("sub", i, step, channel, track)

        self.instance.write(f'slist:sequence:delete "{seqname}"')
        self.instance.write(f'slist:sequence:new "{seqname}",2,{tracks}')
        self.instance.write(f'slist:sequence:step2:goto "{seqname}",first')
        self.instance.write(f'slist:sequence:event:jtiming "{seqname}", immediate')

        # for gating pulse
        self.instance.write(f'slist:sequence:step1:goto "{seqname}", first')
        self.instance.write(f'slist:sequence:step1:ejinput "{seqname}", ATR')
        self.instance.write(f'slist:sequence:step1:ejump "{seqname}", 2')
        for track, channel in enumerate(self.channels, start=1):
            self.instance.write(
                f'slist:sequence:step1:tasset{track}:waveform "{seqname}","{self.wavenames[0]}_{channel}"'
            )

        # for actual seq
        self.instance.write(f'slist:sequence:step2:rcount "{seqname}", {nongatereps}')
        for track in range(1, tracks + 1):
            self.instance.write(f'SLIS:SEQ:STEP2:TASS{track}:SEQ "{seqname}","sub"')

        for track, channel in enumerate(self.channels, start=1):
            self.instance.write(f'SOURCE{channel}:CASS:SEQUENCE "{seqname}",{track}')
        self.opc_wait()
        logger.info("Setting up sequencer".ljust(65, ".") + "[done]")

    def _sequence_repeated_child(
        self, seqname: str, logical_steps: list[AWGSequenceStep], nongatereps: int = 1
    ) -> None:
        # This parent/child sequencer upload uses Tek AWG70000-series SCPI and
        # was tested on TEKTRONIX AWG70001B firmware FV:8.1.0266.0. Similar Tek
        # models should be verified before enabling repeated_child in the lab.
        prefix, child, repetitions, suffix = self._validate_repeated_child_region(
            logical_steps
        )
        compact_body_steps = len(prefix) + 1 + len(suffix)
        top_level_steps = 1 + compact_body_steps
        logger.info(
            "Tek AWG sequence mode: repeated_child; expanded_logical_steps=%s; "
            "prefix_steps=%s; child_steps=%s; child_repetitions=%s; "
            "suffix_steps=%s; compact_measurement_steps=%s; top_level_steps=%s; "
            "unique_waveforms=%s",
            len(logical_steps),
            len(prefix),
            len(child),
            repetitions,
            len(suffix),
            compact_body_steps,
            top_level_steps,
            len(set(self.wavenames)),
        )
        # Keep waveform memory unchanged: the child is a sequence asset that
        # references already-uploaded waveforms, not a concatenated waveform.
        tracks = len(self.channels)
        child_sequence = "child"
        top_sequence = seqname

        self.instance.write(f'slist:sequence:delete "{child_sequence}"')
        self.instance.write(
            f'slist:sequence:new "{child_sequence}",{len(child)},{tracks}'
        )
        self.instance.write(
            f'slist:sequence:event:jtiming "{child_sequence}", immediate'
        )
        for step_index, step in enumerate(child, start=1):
            for track, channel in enumerate(self.channels, start=1):
                self._configure_waveform_step(
                    child_sequence, step_index, step, channel, track
                )

        self.instance.write(f'slist:sequence:delete "{top_sequence}"')
        self.instance.write(
            f'slist:sequence:new "{top_sequence}",{top_level_steps},{tracks}'
        )
        self.instance.write(f'slist:sequence:event:jtiming "{top_sequence}", immediate')
        self.instance.write(f'slist:sequence:step1:goto "{top_sequence}", first')
        self.instance.write(f'slist:sequence:step1:ejinput "{top_sequence}", ATR')
        self.instance.write(f'slist:sequence:step1:ejump "{top_sequence}", 2')
        for track, channel in enumerate(self.channels, start=1):
            self.instance.write(
                f'slist:sequence:step1:tasset{track}:waveform "{top_sequence}","{self.wavenames[0]}_{channel}"'
            )

        body_step = 2
        # The top-level sequence preserves the existing trigger-wait step and
        # then runs prefix + repeated child + suffix directly. Avoiding
        # wrapper -> body -> child nesting keeps this compatible with AWG70000B.
        for step in prefix:
            for track, channel in enumerate(self.channels, start=1):
                self._configure_waveform_step(
                    top_sequence, body_step, step, channel, track
                )
            body_step += 1
        self.instance.write(
            f'slist:sequence:step{body_step}:rcount "{top_sequence}",{repetitions}'
        )
        for track in range(1, tracks + 1):
            self.instance.write(
                f'SLIS:SEQ:STEP{body_step}:TASS{track}:SEQ "{top_sequence}","{child_sequence}"'
            )
        body_step += 1
        for step in suffix:
            for track, channel in enumerate(self.channels, start=1):
                self._configure_waveform_step(
                    top_sequence, body_step, step, channel, track
                )
            body_step += 1
        self.instance.write(
            f'slist:sequence:step{top_level_steps}:goto "{top_sequence}", first'
        )
        for track, channel in enumerate(self.channels, start=1):
            self.instance.write(
                f'SOURCE{channel}:CASS:SEQUENCE "{top_sequence}",{track}'
            )
        self.opc_wait()
        logger.info("Setting up sequencer".ljust(65, ".") + "[done]")

    def _configure_waveform_step(
        self,
        sequence_name: str,
        step_number: int,
        step: AWGSequenceStep,
        channel: int,
        track: int,
    ) -> None:
        self.instance.write(
            f'slist:sequence:step{step_number}:rcount "{sequence_name}",{step.repeat}'
        )
        self.instance.write(
            f'slist:sequence:step{step_number}:tasset{track}:waveform "{sequence_name}","{step.waveform}_{channel}"'
        )
        # Resolve physical channels to tracks through the configured ordering.
        # Combine overlapping legacy/qualified selectors before writing each flag.
        targeted_flags = {
            flag
            for selector in self.flag_channels
            for flag, source in [parse_awg_flag(selector, self.channels)]
            if source is None or source == channel
        }
        present_flags = {
            flag
            for selector in step.flags
            for flag, source in [parse_awg_flag(selector, self.channels)]
            if source is None or source == channel
        }
        for flag in sorted(targeted_flags):
            flag_value = "HIGH" if flag in present_flags else "LOW"
            self.instance.write(
                f'slist:sequence:step{step_number}:tflag{track}:{flag}flag "{sequence_name}",{flag_value}'
            )

    def _get_awg_sequence_mode(self) -> str:
        return str(self.sequence_options.get("awg_sequence_mode", "flat"))

    # Thin wrappers around the shared functions with this synchroniser's limits.
    def _validate_repeated_child_region(
        self, steps: list[AWGSequenceStep]
    ) -> tuple[
        list[AWGSequenceStep], list[AWGSequenceStep], int, list[AWGSequenceStep]
    ]:
        return validate_repeated_child_region(
            steps,
            self.sequence_options.get("awg_repeated_child", {}),
            max_sequence_steps=self.awg_max_sequence_steps,
            max_step_repeat=self.awg_max_step_repeat,
            device_type=self.device_type,
        )

    def _validate_awg_repeat_count(self, repeat: int, context: str) -> None:
        validate_awg_repeat_count(
            repeat, self.awg_max_step_repeat, context, self.device_type
        )

    def _infer_child_repetitions(
        self,
        steps: list[AWGSequenceStep],
        prefix_steps: int,
        child: list[AWGSequenceStep],
    ) -> int:
        return infer_child_repetitions(steps, prefix_steps, child)

    def _get_logical_awg_steps(self) -> list[AWGSequenceStep]:
        validate_awg_metadata_lengths(self.wavenames, self.seqrepeats, "Tek AWG")
        self._validate_flag_channels()
        for selectors in self.flag_values.values():
            for selector in selectors:
                parse_awg_flag(selector, self.channels)
        return build_logical_awg_steps(
            self.wavenames,
            self.seqrepeats,
            AWGSequenceStep,
            max_step_repeat=self.awg_max_step_repeat,
            device_type="Tek AWG",
            flag_values=self.flag_values,
        )

    def _load_awg_sequence_options(self, ps_yaml_file: Path) -> dict[str, Any]:
        with open(ps_yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        validate_sequence_instructions(sequence_instructions)
        return get_awg_sequence_options(sequence_instructions)

    def _read_awg_errors(self) -> list[str]:
        errors = []
        # Bounded: an instrument that never reports "0,No error" must not hang QuPyt.
        for _ in range(100):
            response = str(self.instance.query("SYSTEM:ERROR?")).strip()
            code_text, _, message = response.partition(",")
            try:
                code = int(code_text)
            except ValueError:
                errors.append(response)
                break
            if code == 0:
                break
            errors.append(f"{code},{message}".rstrip(","))
        else:
            errors.append("AWG error queue did not drain after 100 reads")
        return errors

    def _raise_on_awg_errors(self, context: str) -> None:
        errors = self._read_awg_errors()
        if errors:
            raise RuntimeError(
                f"Tektronix AWG reported errors during {context}: " + "; ".join(errors)
            )

    def _load_sequence_block(self, seqname: Path) -> None:
        block = np.load(seqname)
        # Close the .npz file handle even if an entry is missing.
        with cleanup_on_exit(block.close, "numeric sequence file close"):
            self.waveform_block = block["arr_0"]
            self.seqrepeats = list(block["arr_1"])
            self.wavenames = list(block["arr_2"])
            self.flag_values = pickle.loads(block["arr_5"])
        logger.info("loaded wave sequence from file".ljust(65, ".") + f"{seqname}")

    def _clear_awg(self) -> None:
        self.instance.write("slist:sequence:delete all")
        self.instance.write("wlist:waveform:delete all")
        logger.info("Clear all AWG slist and wlist".ljust(65, ".") + "[done]")
        self.opc_wait()

    def _upload_waveforms(self) -> None:
        time_1 = time()
        sorted_wavenames = sorted(set(self.wavenames))
        for i, wavename in tqdm(
            enumerate(sorted_wavenames),
            total=len(sorted_wavenames),
            ascii=True,
            desc="uploading waveforms",
        ):
            for channel_index, channel in enumerate(self.channels):
                self._upload_waveform(
                    f"{wavename}_{channel}",
                    self.waveform_block[i, channel_index * 2 : (channel_index * 2) + 2],
                )
                self.opc_wait()
        logger.info(
            f"Uploaded Tektronix AWG waveforms in {time() - time_1} seconds".ljust(
                65, "."
            )
            + "[done]"
        )

    def _set_output_on(self, channel: int) -> None:
        self.instance.write(f"outp{channel} on")
        logger.info(f"Set channel{channel} output to on".ljust(65, ".") + "[done]")

    def _set_daq_resolution(self, channel: int, dac_resolution: int) -> None:
        self.instance.write(f"source{channel}:dac:resolution {dac_resolution}")
        logger.info(
            f"Set AWG channel{channel} resolution to bit".ljust(65, ".")
            + f"{dac_resolution}"
        )

    def _set_sampling_rate(self) -> None:
        self.instance.write(f"source:frequency {self.samprate}")
        logger.info("AWG sampling rate".ljust(65, ".") + f"{self.samprate}")
        self.opc_wait()

    def _set_marker_amplitude(
        self, channel: int, marker: int, voltage: float = 1.75
    ) -> None:
        self.instance.write(
            f"SOURCE{channel}:MARKER{marker}:VOLTAGE:LEVEL:IMMEDIATE:HIGH {voltage}"
        )
        self.opc_wait()
        logger.info(
            f"Set AWG channel{channel} marker{marker} to / V".ljust(65, ".")
            + f"{voltage}"
        )

    def _set_analog_amplitude(self, channel: int, amplitude: float = 1.0) -> None:
        # amplitude is given in fractions of the max amplitude.
        self.instance.write(
            f"source{channel}:voltage:level:immediate:amplitude {amplitude}"
        )
        self.opc_wait()
        logger.info(
            f"Set AWG channel{channel} analog amplitude to".ljust(65, ".")
            + f"{amplitude}"
        )

    def _set_device_type(self, device_type: str) -> None:
        self.device_type = device_type

    def _set_sampling_rate_attribute(self, sampling_rate: float) -> None:
        self.samprate = sampling_rate

    def _set_channels_attribute(self, channels: list[int]) -> None:
        self.channels = channels

    def _set_awg_max_sequence_steps(self, max_sequence_steps: int) -> None:
        self.awg_max_sequence_steps = validate_awg_limit(
            max_sequence_steps, "awg_max_sequence_steps", "Tek AWG"
        )

    def _set_awg_max_step_repeat(self, max_step_repeat: int) -> None:
        self.awg_max_step_repeat = validate_awg_limit(
            max_step_repeat, "awg_max_step_repeat", "Tek AWG"
        )

    def _limit_channels_to_available_outputs(self) -> None:
        try:
            identity = self._query_awg_identity()
        except Exception:
            logger.warning(
                "Could not query AWG identity for output channel detection",
                exc_info=True,
            )
            raise
        available_channels = self._detect_awg70000_available_channels(identity)
        if available_channels is None:
            return
        usable_channels = [
            channel for channel in self.channels if channel in available_channels
        ]
        if not usable_channels:
            raise ValueError(
                "Configured Tek AWG channels do not match the detected "
                f"AWG70000-series outputs. channels={self.channels}, "
                f"available_channels={available_channels}, idn={identity.strip()!r}."
            )
        if usable_channels != self.channels:
            message = (
                "Restricting Tek AWG channels to detected AWG70000-series "
                f"outputs: requested {self.channels}, available "
                f"{available_channels}, using {usable_channels}, "
                f"idn={identity.strip()!r}."
            )
            logger.warning(message)
            self.channels = usable_channels

    def _detect_awg70000_available_channels(self, identity: str) -> list[int] | None:
        """Return available output channels for known Tek AWG70000-series models.

        The AWG70001 is single-output, while AWG70002 models have two outputs.
        For unknown models, return None and leave the user-configured channels
        unchanged instead of guessing hardware capabilities.
        """
        if "AWG70001" in identity:
            return [1]
        if "AWG70002" in identity:
            return [1, 2]
        return None

    def _query_awg_identity(self) -> str:
        return str(self.instance.query("*IDN?"))

    def _raise_if_repeated_child_is_unverified_model(self) -> None:
        try:
            identity = self._query_awg_identity()
        except Exception:
            logger.warning(
                "Could not query AWG identity for repeated-child model verification",
                exc_info=True,
            )
            raise
        if "AWG5202" in identity:
            message = (
                "awg_sequence_mode: repeated_child is not enabled for detected "
                f"AWG model {identity.strip()!r}. The current repeated_child "
                "SCPI path was tested on TEKTRONIX AWG70001B and uses "
                "AWG70000-series sequence asset/track commands. AWG5202 may be "
                "similar, but it must be verified with the programming manual "
                "or a hardware test before this guard is removed. Use flat mode "
                "on AWG5202 for now."
            )
            raise ValueError(message)

    def plot_waveform(self, wavename: str) -> None:
        """Query and plot the values that were previously uploaded to the AWG.

        This can be very usefull to verify, that the upload worked as expected.

        Parameters
        ----------
        wavename : str
            Name of the uploaded waveform, including the channel
            suffix it was uploaded with (``<block>_<channel>``, e.g. ``"block_0_1"``).
        """
        try:
            import matplotlib.pyplot as plt  # pylint: disable=import-outside-toplevel
        except ImportError as e:
            raise ImportError(
                "Plotting needs 'matplotlib': pip install qupyt[plot]"
            ) from e

        marker = self.instance.query_binary_values(
            'wlist:waveform:marker:data? "' + wavename + '"', datatype="B"
        )
        analog = self.instance.query_binary_values(
            'wlist:waveform:data? "' + wavename + '"'
        )
        # Markers are bits 7..4 of each marker byte; markers[0] is bit 7.
        marker_bytes = np.asarray(marker, dtype=np.uint8)
        markers = np.array([(marker_bytes >> bit) & 1 for bit in (7, 6, 5, 4)])
        logger.debug(
            "marker shape %s, analog shape %s", np.shape(markers), np.shape(analog)
        )

        plt.plot(np.asarray(analog) * 0.5 + 4)
        plt.plot(markers[0, :] * 0.5 + 3)
        plt.plot(markers[1, :] * 0.5 + 2)
        plt.plot(markers[2, :] * 0.5 + 1)
        plt.plot(markers[3, :] * 0.5 + 0)
        plt.show()
