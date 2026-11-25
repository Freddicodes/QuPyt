import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Self

import numpy as np
import yaml
from termcolor import colored
from tqdm import tqdm

from qupyt.config import get_settings
from qupyt.hardware.visa_handler import VisaObject
from qupyt.pulse_sequences.sequence_designer import (
    PulseSequenceYaml,
    parse_awg_flag,
)
from qupyt.utils.imports import install_hint

from .base import Synchroniser, _validate_awg_limit

logger = logging.getLogger(__name__)


class AWGenerator(VisaObject, Synchroniser, synchroniser_types=("TekAWG",)):
    """
    Synchroniser implementation for Tektronix AWGs.

    Flat mode preserves the existing Tek AWG behavior. repeated_child mode uses
    Tek AWG70000-series sequence-asset SCPI and was tested on AWG70001B.
    """

    @dataclass(frozen=True)
    class AWGSequenceStep:
        """One expanded logical AWG sequencer step.

        Repeated-child compression must compare all hardware-visible state, not just
        waveform names; repeat counts and flag states are part of the timing contract.
        """

        waveform: str
        repeat: int
        flags: tuple[str, ...]

    def __init__(
        self, configuration: dict[str, Any], channel_mapping: dict[str, Any]
    ) -> None:
        self.device_type: str = "TekAWG"
        self.samprate: float = 5e9
        self.channel_mapping = channel_mapping
        self.flag_channels = self._extract_flags(channel_mapping)

        self.wavenames: list[str]
        self.seqrepeats: list[int]
        self.waveform_block: np.ndarray
        self.sequence_options: dict[str, Any] = {}
        self.analog_amplitude: float = 1.0
        self.marker_amplitude: float = 1.75
        self.dac_resolution: int = 12
        self.channels: list[int] = [1, 2]
        self.marker_channels: list[int] = [1, 2, 3, 4]
        self.awg_max_sequence_steps: int = 16_384
        self.awg_max_step_repeat: int = 1_048_576

        Synchroniser.__init__(self, configuration, channel_mapping)
        self.attribute_map["device_type"] = self._set_device_type
        self.attribute_map["sampling_rate"] = self._set_sampling_rate_attribute
        self.attribute_map["channels"] = self._set_channels_attribute
        self.attribute_map["awg_max_sequence_steps"] = self._set_awg_max_sequence_steps
        self.attribute_map["awg_max_step_repeat"] = self._set_awg_max_step_repeat
        if configuration is not None:
            self._update_from_configuration(configuration)
        VisaObject.__init__(self, self.address, self.device_type)
        self.instance.timeout = 20000
        self._limit_channels_to_available_outputs()

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
        """Passing"""

    def run(self) -> None:
        self.instance.write("awgcontrol:run:immediate")
        logger.info("Turned on AWG output to RUN immediate".ljust(65, ".") + "[done]")
        self.opc_wait()

    def stop(self) -> None:
        self.instance.write("awgcontrol:stop:immediate")
        logger.info("Turned on AWG output to STOP immediate".ljust(65, ".") + "[done]")
        self.opc_wait()

    def trigger(self) -> None:
        self.instance.write("trigger:immediate atrigger")
        logger.info("Sent trigger to AWG".ljust(65, ".") + "[done]")

    def load_sequence(self, ps_yaml_file: str = "sequence_0.yaml") -> None:
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
        self._load_sequence_block(get_settings().sequence_dir / "sequence.npz")
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
        print("\nConfiguring AWG".ljust(65, ".") + colored(" [done]", "green"))

    def _upload_waveform(self, wavename: str, waveform: np.ndarray) -> None:
        self.instance.write('wlist:waveform:delete "' + wavename + '"')
        self.instance.write(
            'wlist:waveform:new "' + wavename + '",' + str(waveform.shape[1]) + ",real"
        )
        self.instance.write_binary_values(
            'wlist:waveform:data "' + wavename + '",', waveform[0, :]
        )
        self.instance.write_binary_values(
            'wlist:waveform:marker:data "' + wavename + '",',
            waveform.astype(np.uint8)[1, :],
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
        self,
        seqname: str,
        logical_steps: list[Self.AWGSequenceStep],
        nongatereps: int = 1,
    ) -> None:
        if len(logical_steps) > self.awg_max_sequence_steps:
            raise ValueError(
                f"Expanded AWG sequence has {len(logical_steps)} steps, exceeding "
                f"the configured AWG limit of {self.awg_max_sequence_steps}. Enable "
                "awg_sequence_mode: repeated_child or reduce the sequence."
            )
        summary = f"Tek AWG sequence mode: flat\nLogical steps: {len(logical_steps)}"
        print(summary)
        logger.info("Tek AWG sequence mode: flat; logical_steps=%s", len(logical_steps))
        print("Setting up sequencer".ljust(65, "."), end="")
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
        print(colored(" [done]", "green"))

    def _sequence_repeated_child(
        self,
        seqname: str,
        logical_steps: list[Self.AWGSequenceStep],
        nongatereps: int = 1,
    ) -> None:
        # This parent/child sequencer upload uses Tek AWG70000-series SCPI and
        # was tested on TEKTRONIX AWG70001B firmware FV:8.1.0266.0. Similar Tek
        # models should be verified before enabling repeated_child in the lab.
        prefix, child, repetitions, suffix = self._validate_repeated_child_region(
            logical_steps
        )
        compact_body_steps = len(prefix) + 1 + len(suffix)
        top_level_steps = 1 + compact_body_steps
        summary = (
            "Tek AWG sequence mode: repeated_child\n"
            f"Expanded logical steps: {len(logical_steps)}\n"
            f"Prefix steps: {len(prefix)}\n"
            f"Child steps: {len(child)}\n"
            f"Child repetitions: {repetitions}\n"
            f"Suffix steps: {len(suffix)}\n"
            f"Compact measurement steps: {compact_body_steps}\n"
            f"Top-level steps: {top_level_steps}\n"
            f"Unique waveforms: {len(set(self.wavenames))}"
        )
        print(summary)
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
        print("Setting up sequencer".ljust(65, "."), end="")
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
                    child_sequence,
                    step_index,
                    step,
                    channel,
                    track,
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
        print(colored(" [done]", "green"))

    def _configure_waveform_step(
        self,
        sequence_name: str,
        step_number: int,
        step: Self.AWGSequenceStep,
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

    def _get_logical_awg_steps(self) -> list[Self.AWGSequenceStep]:
        if len(self.wavenames) != len(self.seqrepeats):
            raise ValueError(
                "Tek AWG sequence metadata mismatch: "
                f"{len(self.wavenames)} waveform names but "
                f"{len(self.seqrepeats)} repeat counts."
            )
        self._validate_flag_channels()
        for selectors in self.flag_values.values():
            for selector in selectors:
                parse_awg_flag(selector, self.channels)
        steps = []
        for wavename, repeat in zip(self.wavenames, self.seqrepeats):
            repeat = int(repeat)
            self._validate_awg_repeat_count(repeat, f"step {len(steps) + 1}")
            steps.append(
                Self.AWGSequenceStep(
                    waveform=str(wavename),
                    repeat=repeat,
                    flags=tuple(sorted(self.flag_values.get(wavename, []))),
                )
            )
        return steps

    def _validate_awg_repeat_count(self, repeat: int, context: str) -> None:
        if repeat <= 0:
            raise ValueError(f"Tek AWG {context} repeat count must be positive.")
        if repeat > self.awg_max_step_repeat:
            raise ValueError(
                f"Tek AWG {context} repeat count {repeat} exceeds the "
                f"configured AWG limit of {self.awg_max_step_repeat}."
            )

    def _validate_repeated_child_region(
        self, steps: list[Self.AWGSequenceStep]
    ) -> tuple[
        list[Self.AWGSequenceStep],
        list[Self.AWGSequenceStep],
        int,
        list[Self.AWGSequenceStep],
    ]:
        config = self.sequence_options.get("awg_repeated_child", {})
        if not isinstance(config, dict):
            raise ValueError(
                "awg_repeated_child must be a mapping with prefix_steps, "
                "child_steps, and optionally repetitions."
            )
        try:
            prefix_steps = int(config.get("prefix_steps", 0))
            child_steps = int(config["child_steps"])
        except KeyError as exc:
            raise ValueError(
                "awg_repeated_child requires child_steps for repeated_child mode."
            ) from exc
        repetitions_config = config.get("repetitions")
        if prefix_steps < 0:
            raise ValueError(
                f"awg_repeated_child prefix_steps must be >= 0, got {prefix_steps}."
            )
        if child_steps <= 0:
            raise ValueError(
                f"awg_repeated_child child_steps must be > 0, got {child_steps}."
            )
        if child_steps > self.awg_max_sequence_steps:
            raise ValueError(
                f"Repeated child sequence has {child_steps} steps, exceeding "
                f"the configured AWG limit of {self.awg_max_sequence_steps}."
            )
        if prefix_steps + child_steps > len(steps):
            raise ValueError(
                "awg_repeated_child prefix_steps + child_steps exceeds the "
                f"logical sequence length: {prefix_steps} + {child_steps} > "
                f"{len(steps)}."
            )
        child = steps[prefix_steps : prefix_steps + child_steps]
        if repetitions_config is None:
            repetitions = self._infer_child_repetitions(steps, prefix_steps, child)
        else:
            repetitions = int(repetitions_config)
            if repetitions <= 0:
                raise ValueError(
                    f"awg_repeated_child repetitions must be > 0, got {repetitions}."
                )
            expected_end = prefix_steps + repetitions * child_steps
            if expected_end > len(steps):
                available_steps = len(steps) - prefix_steps
                max_complete_repetitions = available_steps // child_steps
                raise ValueError(
                    "awg_repeated_child does not match the expanded logical "
                    "sequence. The configured repeated region would end after "
                    f"{prefix_steps} + {repetitions} * {child_steps} = "
                    f"{expected_end} steps, but the generated sequence has only "
                    f"{len(steps)} steps. After the {prefix_steps} prefix steps, "
                    f"only {available_steps} steps remain, which can contain at "
                    f"most {max_complete_repetitions} complete child repetitions "
                    f"of {child_steps} steps. Check that n_meas/repetitions and "
                    "len(cycle_order)/child_steps are calculated from the same "
                    "sequence that is written to sequencing_order."
                )
            for repetition_index in range(repetitions):
                start = prefix_steps + repetition_index * child_steps
                current_child = steps[start : start + child_steps]
                if current_child != child:
                    raise ValueError(
                        "Repeated child block mismatch at repetition "
                        f"{repetition_index + 1}. Waveform names, repeat counts, "
                        "and flags must match exactly for repeated_child mode."
                    )
        self._validate_awg_repeat_count(repetitions, "child-sequence parent")
        suffix_start = prefix_steps + repetitions * child_steps
        suffix = steps[suffix_start:]
        compact_body_steps = prefix_steps + 1 + len(suffix)
        if compact_body_steps > self.awg_max_sequence_steps:
            raise ValueError(
                f"Compact AWG parent/body sequence has {compact_body_steps} steps, "
                f"exceeding the configured AWG limit of {self.awg_max_sequence_steps}."
            )
        return steps[:prefix_steps], child, repetitions, suffix

    def _infer_child_repetitions(
        self,
        steps: list[Self.AWGSequenceStep],
        prefix_steps: int,
        child: list[Self.AWGSequenceStep],
    ) -> int:
        child_steps = len(child)
        repetitions = 0
        start = prefix_steps
        while steps[start : start + child_steps] == child:
            repetitions += 1
            start += child_steps
        if repetitions < 2:
            raise ValueError(
                "Could not infer awg_repeated_child repetitions: at least two "
                "identical child occurrences are required."
            )
        return repetitions

    def _load_awg_sequence_options(self, ps_yaml_file: Path) -> dict[str, Any]:
        yaml_file = Path(ps_yaml_file)
        if not yaml_file.is_absolute():
            yaml_file = get_settings().sequence_dir / yaml_file
        with open(yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        return {
            key: sequence_instructions[key]
            for key in ("awg_sequence_mode", "awg_repeated_child")
            if key in sequence_instructions
        }

    def _read_awg_errors(self) -> list[str]:
        errors = []
        while True:
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
        return errors

    def _raise_on_awg_errors(self, context: str) -> None:
        errors = self._read_awg_errors()
        if errors:
            raise RuntimeError(
                f"Tektronix AWG reported errors during {context}: " + "; ".join(errors)
            )

    def _load_sequence_block(self, seqname: Path) -> None:
        block = np.load(seqname)
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
            + "{dac_resolution}"
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
        self.awg_max_sequence_steps = _validate_awg_limit(
            max_sequence_steps,
            "awg_max_sequence_steps",
            "Tek AWG",
        )

    def _set_awg_max_step_repeat(self, max_step_repeat: int) -> None:
        self.awg_max_step_repeat = _validate_awg_limit(
            max_step_repeat,
            "awg_max_step_repeat",
            "Tek AWG",
        )

    def _limit_channels_to_available_outputs(self) -> None:
        try:
            identity = self._query_awg_identity()
        except Exception:
            return
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
            print(message)
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
            return
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
            logger.error(message)
            raise ValueError(message)

    def plot_waveform(self, wavename: str) -> None:
        """
        Convenience function to query and plot the values that were previously
        uploaded to the AWG. This can be very usefull to verify, that
        the upload worked as expected.

        :param wavename: Identifier of the
         uploaded sequence block to query and plot.
        :type wavename: str
        """
        with install_hint("tekawg"):
            import matplotlib.pyplot as plt

        marker = self.instance.query_binary_values(
            'wlist:waveform:marker:data? "' + wavename + '"', datatype="B"
        )
        analog = self.instance.query_binary_values(
            'wlist:waveform:data? "' + wavename + '"'
        )
        markers = np.zeros((4, len(marker)))
        print(np.shape(markers), np.shape(analog))
        for i, mark in enumerate(marker):
            num = bin(mark)[2:]
            if not len(num) < 5:
                for j in range(1, 5):
                    try:
                        markers[-j, i] = int(num[-(j + 4)])
                    except Exception:
                        break

        plt.plot(np.asarray(analog) * 0.5 + 4)
        plt.plot(markers[0, :] * 0.5 + 3)
        plt.plot(markers[1, :] * 0.5 + 2)
        plt.plot(markers[2, :] * 0.5 + 1)
        plt.plot(markers[3, :] * 0.5 + 0)
        plt.show()
