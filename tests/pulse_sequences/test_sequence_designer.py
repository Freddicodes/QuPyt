from pathlib import Path
from typing import Any

import yaml

from qupyt.pulse_sequences.sequence_designer import PulseBlasterSequence


def _write_sequence(tmp_path: Path, block: Any, total_duration: Any) -> Any:
    path = tmp_path / "sequence.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "total_duration": total_duration,
                "sequencing_order": ["block"],
                "sequencing_repeats": [1],
                "block": block,
            }
        ),
        encoding="utf-8",
    )
    return path


def _compile(
    tmp_path: Path, channel_mapping: Any, block: Any, total_duration: Any
) -> Any:
    sequence = PulseBlasterSequence(
        channel_mapping, _write_sequence(tmp_path, block, total_duration)
    )
    sequence.parse_pulse_sequence_file()
    return sequence.compile()


def test_single_pulse(tmp_path: Path) -> None:
    block = {"A": {"p0": {"start": 1, "duration": 2}}}
    assert _compile(tmp_path, {"A": 0}, block, 5) == ([0, 1, 0], [1, 2, 2])


def test_channels_switching_together_drop_all_zero_length_segments(
    tmp_path: Path,
) -> None:
    # Three channels switch on at 0 and off at 1: four zero-length segments.
    # Popping them in ascending order shifted the indices (wrong entries
    # removed, then IndexError).
    pulse = {"p0": {"start": 0, "duration": 1}}
    block = {"A": pulse, "B": pulse, "C": pulse}
    mapping = {"A": 0, "B": 1, "C": 2}
    assert _compile(tmp_path, mapping, block, 2) == ([7, 0], [1, 1])


def test_two_simultaneous_edges_keep_the_right_segments(tmp_path: Path) -> None:
    block = {
        "A": {"p0": {"start": 0, "duration": 2}},
        "B": {"p0": {"start": 1, "duration": 1}},
        "C": {"p0": {"start": 1, "duration": 2}},
    }
    mapping = {"A": 0, "B": 1, "C": 2}
    # 0-1: A; 1-2: A+B+C; 2-3: C; 3-4: off
    assert _compile(tmp_path, mapping, block, 4) == ([1, 7, 4, 0], [1, 1, 1, 1])
