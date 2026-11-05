"""Translate pulse sequence YAML files into synchroniser-specific programs.

- :class:`PulseSequenceYaml` + :class:`PulseSequence`: sampled waveforms
  (analog + 4 markers per source) for the Tektronix AWG, written to
  ``sequence.npz`` in the sequence directory.
- :class:`PulseBlasterSequence`: channel bit masks and segment durations
  for the SpinCore PulseBlaster.

The YAML layout (``total_duration``, ``sequencing_order``,
``sequencing_repeats`` and one mapping per block: channel → pulses with
``start``, ``duration``, ``amplitude``, ``frequency``, ``phase``; times in
µs) is produced by :mod:`qupyt.pulse_sequences.yaml_sequence`.
"""

import hashlib
import logging
import operator
import pickle
import re
from pathlib import Path
from typing import Any, cast

import numpy as np
import yaml

from qupyt.set_up import get_seq_dir

logger = logging.getLogger(__name__)

# Bump when numeric compilation changes so older artifacts are regenerated.
NUMERIC_SEQUENCE_CACHE_VERSION = 1


def validate_sequence_instructions(instructions: object) -> None:
    """Validate the block structure shared by every synchroniser.

    Parameters
    ----------
    instructions : object
        Parsed pulse-sequence YAML.

    Raises
    ------
    ValueError
        If the YAML is not a mapping, ``sequencing_order`` and
        ``sequencing_repeats`` are not nonempty lists of equal length, a
        referenced block is missing, or a repeat count is not a positive int.
    """
    if not isinstance(instructions, dict):
        raise ValueError("Pulse-sequence instructions must contain a YAML mapping")
    order = instructions.get("sequencing_order")
    repeats = instructions.get("sequencing_repeats")
    if (
        not isinstance(order, list)
        or not isinstance(repeats, list)
        or not order
        or len(order) != len(repeats)
    ):
        raise ValueError(
            "sequencing_order and sequencing_repeats must be nonempty lists of "
            "equal length"
        )
    for block, repeat in zip(order, repeats, strict=True):
        if not isinstance(block, str) or not isinstance(instructions.get(block), dict):
            raise ValueError(f"Missing or invalid pulse-sequence block {block!r}")
        if not isinstance(repeat, int) or repeat < 1:
            raise ValueError(
                f"Repeat count for block {block!r} must be a positive integer; "
                f"got {repeat!r}"
            )


def parse_awg_flag(selector: str, channels: list[int]) -> tuple[str, int | None]:
    """Parse a flag selector; its suffix is a physical AWG source channel."""
    if (
        not isinstance(selector, str)
        or re.fullmatch(r"[ABCD](?:[12])?", selector) is None
    ):
        raise ValueError(
            f"Invalid AWG flag selector {selector!r}; expected A, B, C, D "
            "or a flag followed by physical AWG channel 1 or 2 (for example A2)."
        )
    channel = int(selector[1]) if len(selector) == 2 else None
    if channel is not None and channel not in channels:
        raise ValueError(
            f"AWG flag selector {selector!r} requests inactive AWG channel "
            f"{channel}; active channels are {channels}."
        )
    return selector[0], channel


class PulseSequenceYaml:
    """Turn a pulse sequence YAML file into AWG waveforms (``sequence.npz``).

    Parameters
    ----------
    channel_mapping
        Channel name → row in :class:`PulseSequence`
        (int, see :meth:`PulseSequence.add_pulse`) or an AWG flag selector
        (str, e.g. ``"A"`` or ``"A2"``, see :func:`parse_awg_flag`).
    awg_sources
        AWG source channels in use, e.g. ``[1, 2]``.
    samprate
        Sampling rate in samples per second.
    yaml_file
        Pulse sequence file; default
        ``<sequence dir>/sequence_0.yaml``.
    """

    def __init__(
        self,
        #  Channel list as you think about them
        #  marker 1, marker 2 and so on
        channel_mapping: dict[str, int | str],
        #  Source as the AWG thinks about them
        #  e.g. source1 might include analog 1, marker 1, ...
        #  There is one analog channel per source but multiple markers etc.
        awg_sources: list[int],
        samprate: float,  # = 5e9,
        yaml_file: str | Path | None = None,
    ) -> None:
        self.yaml_file = Path("sequence_0.yaml" if yaml_file is None else yaml_file)
        if not self.yaml_file.is_absolute():
            self.yaml_file = get_seq_dir() / self.yaml_file
        self.awg_sources = awg_sources
        self.channel_mapping = channel_mapping
        self.samp_rate = float(samprate)  # samples per second

    def _sequence_didnt_change(self) -> bool:
        with open(self.yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        validate_sequence_instructions(sequence_instructions)
        # Configuration changes (including A -> A2) must regenerate flag metadata
        # even when the pulse YAML is unchanged.
        cache_key = {
            "compiler_version": NUMERIC_SEQUENCE_CACHE_VERSION,
            "sequence": sequence_instructions,
            "channel_mapping": self.channel_mapping,
            "awg_sources": self.awg_sources,
            "sampling_rate": self.samp_rate,
        }
        self._cache_instructions = cache_key
        # All YAML inputs compile into the same artifact. Keep its cache beside
        # that artifact so alternating YAML files cannot reuse another sequence.
        self._cache_path = get_seq_dir() / "sequence.npz.aux"
        try:
            artifact = (get_seq_dir() / "sequence.npz").stat()
            with self._cache_path.open("r", encoding="utf-8") as file:
                previous = yaml.safe_load(file)
        except FileNotFoundError:
            return False
        except yaml.YAMLError:
            logger.warning(
                "Invalid pulse-sequence cache %s; recompiling",
                self._cache_path,
                exc_info=True,
            )
            return False
        return isinstance(previous, dict) and previous == {
            "instructions": cache_key,
            "artifact": [artifact.st_mtime_ns, artifact.st_size],
        }

    def _record_compiled_sequence(self) -> None:
        """Record the cache entry only after the numeric artifact was saved."""
        artifact = (get_seq_dir() / "sequence.npz").stat()
        with self._cache_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(
                {
                    "instructions": self._cache_instructions,
                    "artifact": [artifact.st_mtime_ns, artifact.st_size],
                },
                file,
            )

    def translate_yaml_to_numeric_instructions(self) -> None:
        """Build the waveforms of all blocks and write ``sequence.npz``.

        Skipped if neither the YAML nor the AWG settings changed since the last
        call (compared with the ``.aux`` file next to the YAML).

        Raises
        ------
        ValueError
            for an invalid flag selector in ``channel_mapping``.
        """
        for selector in self.channel_mapping.values():
            if isinstance(selector, str):
                parse_awg_flag(selector, self.awg_sources)
        logger.info(
            "Translating pulse sequence from %s (sampling rate %s)",
            self.yaml_file,
            self.samp_rate,
        )
        if self._sequence_didnt_change():
            logger.info(
                "Reusing cached numeric pulse sequence for unchanged configuration %s",
                self.yaml_file,
            )
            return
        with open(self.yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        sequence_order = sequence_instructions["sequencing_order"]
        sequencing_repeats = sequence_instructions["sequencing_repeats"]
        duration = float(sequence_instructions["total_duration"])
        sorted_pulse_blocks = sorted(set(sequence_order))
        seq = PulseSequence(
            len(sorted_pulse_blocks),
            duration,
            self.awg_sources,
            samprate=self.samp_rate,
        )
        flag_channels: dict[str, Any] = {}
        for i, block in enumerate(sorted_pulse_blocks):
            flag_channels[block] = []
            for channel, pulses in sequence_instructions[block].items():
                mapped_channel = self.channel_mapping[channel]
                if isinstance(mapped_channel, str):
                    flag_channels[block].append(mapped_channel)
                    continue
                for pulse in pulses.values():
                    seq.add_pulse(
                        i,
                        float(pulse["start"]),
                        float(pulse["duration"]),
                        channel=mapped_channel,
                        inputtype="time",
                        amplitude=float(pulse["amplitude"]),
                        freq=(float(pulse["frequency"]), float(pulse["phase"])),
                    )
        seq.flag_channels = pickle.dumps(flag_channels)
        seq.sequencer = sequencing_repeats
        seq.sequencernames = sequence_order
        seq.make("sequence.npz")
        self._record_compiled_sequence()
        logger.info(
            "Translated pulse sequence from %s (%d block(s), channels %s)",
            self.yaml_file,
            len(sorted_pulse_blocks),
            self.awg_sources,
        )


class PulseSequence:
    """Sampled AWG waveforms for ``numseqs`` sequence blocks.

    ``self.pulses`` has the shape ``(numseqs, 5 * len(awg_sources),
    num_points)``: per source one analog row and four marker rows (see
    :meth:`add_pulse`).

    Parameters
    ----------
    numseqs
        Number of sequence blocks.
    duration
        Length of every block in µs.
    awg_sources
        AWG source channels in use.
    samprate
        Sampling rate in samples per second.
    """

    def __init__(
        self,
        numseqs: int,
        duration: float,
        awg_sources: list[int],
        samprate: float = 2.5e9,
    ) -> None:
        if (
            not np.isfinite(samprate)
            or samprate <= 0
            or not np.isfinite(duration)
            or duration <= 0
            or numseqs < 1
        ):
            raise ValueError(
                "Pulse sequence requires positive sampling rate, duration, and "
                "block count"
            )
        self.samp_rate = samprate  # samples per second
        self.min_time = 1 / samprate
        points = samprate * duration * 1e-6
        self.num_points = round(points)
        if self.num_points < 1:
            raise ValueError("Pulse sequence must contain at least one sampling point")
        # Sample times in µs, spaced exactly 1 / samprate (linspace would
        # stretch the spacing to duration / (num_points - 1)).
        self.time = np.arange(self.num_points) * self.min_time * 1e6
        self.numseqs = numseqs
        self.awg_sources = awg_sources

        if not np.isclose(points, self.num_points, rtol=0.0, atol=1e-9):
            logger.warning(
                "The sequence duration is not an integer multiple of samples".ljust(
                    65, "."
                )
                + "[WARNING]"
            )

        #  5 => 1 for analog, 4 for 8 bit marker.
        self.pulses = np.zeros((numseqs, 5 * len(self.awg_sources), self.num_points))
        # Set from outside before make(): PulseSequenceYaml assigns the
        # YAML's sequencing_repeats / sequencing_order (None otherwise).
        # Any: np.savez must get them unchanged (an object array would need
        # allow_pickle when the AWG loads the .npz).
        self.sequencer: Any = None
        self.sequencernames: Any = None
        self.flag_channels: Any
        self.warning_counter = 0
        self.properties = {"Values": "None"}

    def time_to_index(self, time: float) -> int:
        """Convert a time in µs to a sample index (rounded; rounding is counted)."""
        points = self.samp_rate * time * 1e-6
        if not np.isclose(points, round(points)):
            logger.debug(
                "Time %s us is not an integer multiple of samples: %s rounded to %s",
                time,
                points,
                round(points),
            )
            self.warning_counter += 1
        return round(points)

    def add_pulse(
        self,
        numseq: (
            int | np.integer[Any] | tuple[int, int] | list[int] | np.ndarray[Any, Any]
        ),
        start: float,
        duration: float,
        channel: int = 0,
        inputtype: str = "time",
        amplitude: float = 1.0,
        freq: tuple[float, float] | None = None,
    ) -> None:
        """Write a pulse into ``self.pulses`` (in place, returns nothing).

        Parameters
        ----------
        numseq : int | np.integer[Any] | tuple[int, int] | list[int] | np.ndarray[Any, Any]
            Sequence(s) to write to: an index, or a
            ``(start, stop)`` pair (stop exclusive) for a range.
        start : float
            Pulse start, in µs (``inputtype="time"``) or samples.
        duration : float
            Pulse length, same unit as ``start``.
        channel : int
            Row of ``self.pulses``. Each AWG source has 5 rows:
            ``5 * i`` is the analog output of source ``i``, ``5 * i + 1`` to
            ``5 * i + 4`` are its markers 1-4. So ``0`` / ``5`` are the analog
            outputs of the first / second source.
        inputtype : str
            ``"time"`` (µs, converted with :meth:`time_to_index`)
            or anything else for sample indices.
        amplitude : float
            Scales the pulse (use 1 for markers).
        freq : tuple[float, float] | None
            ``(frequency_Hz, phase_rad)`` for a cosine-modulated
            pulse; ``None`` for a rectangular pulse.

        Raises
        ------
        ValueError
            if ``numseq`` is a sequence that is not a pair, or the
            pulse exceeds the sequence bounds.
        TypeError
            if ``numseq`` is neither an index nor a sequence.
        """
        if inputtype == "time":
            start = self.time_to_index(start)
            duration = self.time_to_index(duration)
        else:
            logger.debug("Interpreting pulse input as a number of sampling points")
        if start < 0 or duration < 0 or start + duration > self.num_points:
            raise ValueError(
                f"Pulse interval [{start}, {start + duration}) exceeds sequence "
                f"bounds [0, {self.num_points})"
            )

        fr: np.ndarray[Any, Any] | float
        if freq is not None:
            frequency, phase = freq
            frequency *= 10**-6
            fr = np.cos(
                2 * np.pi * frequency * self.time[int(start) : int(start + duration)]
                + phase
            )
        else:
            fr = 1

        seq_idx: int | np.integer[Any] | slice
        if isinstance(numseq, (int, np.integer)) and not isinstance(numseq, bool):
            seq_idx = numseq
        elif isinstance(numseq, (tuple, list, np.ndarray)):
            if len(numseq) != 2:
                raise ValueError(
                    "Pulse sequence range must contain a start and stop index"
                )
            seq_idx = slice(operator.index(numseq[0]), operator.index(numseq[1]))
        else:
            raise TypeError(
                f"numseq must be an int or a (start, stop) pair, got {numseq!r}"
            )

        self.pulses[seq_idx, channel, int(start) : int(start + duration)] = (
            amplitude * fr
        )

    def make(self, name: str) -> None:
        """Pack analog and marker rows per source (markers 1-4 → bits 7-4 of one byte) and save them, the sequencer info, a hash, ``properties`` and ``flag_channels`` to ``<sequence dir>/<name>``."""
        if self.warning_counter:
            logger.warning(
                "%s times were rounded to whole samples during pulse sequence generation",
                self.warning_counter,
            )
        else:
            logger.info(
                "No rounding during pulse sequence generation".ljust(65, ".") + "[done]"
            )
        final = np.zeros((self.numseqs, 2 * len(self.awg_sources), self.num_points))
        for source_index, _source in enumerate(self.awg_sources):
            final[:, 2 * source_index, :] = self.pulses[:, 5 * source_index, :]
            final[:, 2 * source_index + 1, :] = (
                self.pulses[:, 5 * source_index + 1, :] * 2**7
                + self.pulses[:, 5 * source_index + 2, :] * 2**6
                + self.pulses[:, 5 * source_index + 3, :] * 2**5
                + self.pulses[:, 5 * source_index + 4, :] * 2**4
            )

        # These digests identify compiled waveforms for cache reuse.
        hash1 = hashlib.sha1(final.tobytes(), usedforsecurity=False).hexdigest()
        hash2 = hashlib.sha1(
            str(self.sequencer).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        hash3 = hashlib.sha1(
            str(self.sequencernames).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        hash4 = hash1 + hash2 + hash3
        finalhash = hashlib.sha1(
            hash4.encode("utf-8"), usedforsecurity=False
        ).hexdigest()

        file_dir = get_seq_dir()
        try:
            np.savez(
                file_dir / name,
                final,
                self.sequencer,
                self.sequencernames,
                finalhash,
                # savez stores a dict as a 0-d object array anyway; explicit for mypy.
                np.asarray(self.properties, dtype=object),
                self.flag_channels,
            )
        except Exception:
            logger.exception(
                "Failed to save numeric pulse sequence to %s", file_dir / name
            )
            raise
        logger.info(f"Pulse sequence written to {name}".ljust(65, ".") + "[done]")
        logger.debug(
            "Numeric pulse sequence %s has shape %s", file_dir / name, final.shape
        )


class PulseBlasterSequence:
    """Turn a pulse sequence YAML file into PulseBlaster instructions.

    Call :meth:`parse_pulse_sequence_file`, then :meth:`compile`.

    Parameters
    ----------
    channel_mapping
        Channel name → PulseBlaster output bit.
    yaml_file
        Pulse sequence file; default
        ``<sequence dir>/sequence_0.yaml``.
    """

    def __init__(
        self, channel_mapping: dict[str, int], yaml_file: str | Path | None = None
    ) -> None:
        self.event_times: list[float] = []
        self.event_durations: list[float] = []
        self.event_channel: list[str] = []
        self.events: list[str] = []
        self.channel_bits: list[int] = []
        self.segment_durations: list[float] = []
        self.channel_mapping = channel_mapping
        self.ps: dict[str, Any] = {}
        tmp_yaml_file = (
            yaml_file if yaml_file is not None else get_seq_dir() / "sequence_0.yaml"
        )
        self.yaml_sequence: dict[str, Any] = self._load_yaml_sequence(tmp_yaml_file)
        self.total_duration = self.yaml_sequence["total_duration"]

    def _load_yaml_sequence(self, path: str | Path) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as file:
            yaml_sequence = yaml.safe_load(file)
        validate_sequence_instructions(yaml_sequence)
        logger.debug("Loaded PulseBlaster pulse sequence from %s", path)
        return cast(dict[str, Any], yaml_sequence)

    def parse_pulse_sequence_file(self) -> None:
        """Compute the segments of every block.

        Sorted switching events, the durations between them and the channel bit
        mask of each segment. Zero-length segments (simultaneous edges) are
        dropped.
        """
        for block in self.yaml_sequence["sequencing_order"]:
            self._parse_block(self.yaml_sequence[block])
            self._sort_pulses()
            self._get_event_durations()
            self._compute_channel_bits()
            self.ps[block] = {
                "channel_bits": self.channel_bits,
                "durations": self.event_durations,
            }
            self._reset_attributes()

    def compile(self) -> tuple[list[int], list[float]]:
        """Combine the blocks in ``sequencing_order`` (each repeated ``sequencing_repeats`` times) into one sequence that can be uploaded to the PulseBlaster card in one go.

        Returns
        -------
        tuple[list[int], list[float]]
            ``(channel_bit_masks, durations_us)``, one entry per segment.
        """
        channel_bits = []
        bits_duration = []
        sequencing_info = zip(
            self.yaml_sequence["sequencing_order"],
            self.yaml_sequence["sequencing_repeats"],
        )
        for sequence_block, block_repeats in sequencing_info:
            channel_bits += self.ps[sequence_block]["channel_bits"] * block_repeats
            bits_duration += self.ps[sequence_block]["durations"] * block_repeats

        logger.info(
            "Compiled PulseBlaster pulse sequence: %d instruction(s), %s us",
            len(channel_bits),
            sum(bits_duration),
        )
        return channel_bits, bits_duration

    def _reset_attributes(self) -> None:
        self.event_times = []
        self.event_durations = []
        self.event_channel = []
        self.events = []
        self.channel_bits = []
        self.segment_durations = []

    def _append_event(self, channel: str, pulse: dict[str, float]) -> None:
        if (
            not np.isfinite(pulse["start"])
            or not np.isfinite(pulse["duration"])
            or pulse["start"] < 0
            or pulse["duration"] < 0
        ):
            raise ValueError(
                f"Pulse on {channel!r} requires finite, nonnegative start and duration"
            )
        self.event_times.append(pulse["start"])
        self.event_times.append(pulse["start"] + pulse["duration"])
        self.event_channel.append(channel)
        self.event_channel.append(channel)
        self.events.append("up")
        self.events.append("down")

    def _parse_channel(self, channel: str, channel_pulses: dict[str, Any]) -> None:
        for pulse in channel_pulses.values():
            self._append_event(channel, pulse)

    def _parse_block(self, ps_block: dict[str, Any]) -> None:
        for channel, channel_pulses in ps_block.items():
            self._parse_channel(channel, channel_pulses)

    def _sort_pulses(self) -> None:
        if not self.event_times:
            raise ValueError(
                "PulseBlaster sequence block must contain at least one pulse"
            )
        times, channels, events = zip(
            *sorted(zip(self.event_times, self.event_channel, self.events))
        )
        self.event_times = list(times)
        self.event_channel = list(channels)
        self.events = list(events)

    def _get_event_durations(self) -> None:
        self.event_durations = [
            i - j for i, j in zip(self.event_times[1:], self.event_times[:-1])
        ]
        if self.total_duration == "ignore":
            pass
        elif self.event_times[-1] > self.total_duration:
            raise ValueError(
                f"Pulse ends at {self.event_times[-1]} us, beyond "
                f"total_duration={self.total_duration} us"
            )
        elif self.event_times[-1] != self.total_duration:
            self.event_durations.append(self.total_duration - self.event_times[-1])

    def _event_to_sign(self, event: str) -> int:
        if event == "up":
            return 1
        if event == "down":
            return -1
        raise ValueError(f'No event {event}, options are "up" or "down"')

    def _compute_channel_bits(self) -> None:
        for i, _ in enumerate(self.event_durations):
            prev_val = self.channel_bits[-1] if self.channel_bits else 0
            self.channel_bits.append(
                prev_val
                + 2 ** (self.channel_mapping[self.event_channel[i]])
                * self._event_to_sign(self.events[i])
            )
        if self.event_times[0] != 0:
            self.channel_bits.insert(0, 0)
            self.event_durations.insert(0, self.event_times[0])
        self._pop_unnecessary_entries()

    def _pop_unnecessary_entries(self) -> None:
        """Remove zero-length segments (several channels switching at once)."""
        indices_to_pop = [
            index
            for index, duration in enumerate(self.event_durations)
            if duration == 0
        ]
        # Pop from the back so the remaining indices stay valid.
        for pop_index in reversed(indices_to_pop):
            self.event_durations.pop(pop_index)
            self.channel_bits.pop(pop_index)
