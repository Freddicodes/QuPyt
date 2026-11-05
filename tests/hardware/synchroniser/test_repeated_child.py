"""Repeated-child validation (validate_repeated_child_region and friends in qupyt/hardware/synchroniser/_awg_common.py).

MockGenerator and AWGenerator both call these functions with their configured
limits. Every test runs against both, which checks that each class takes
awg_max_sequence_steps / awg_max_step_repeat from its configuration the same way.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pyvisa
import yaml
from pyvisa.resources import MessageBasedResource

from qupyt.hardware.synchroniser import Synchroniser
from qupyt.hardware.synchroniser._awg_common import (
    AWGSequenceStep,
    validate_awg_repeat_count,
    validate_repeated_child_region,
)


def step(name: str, repeat: int = 1, flags: tuple[str, ...] = ()) -> AWGSequenceStep:
    return AWGSequenceStep(name, repeat, flags)


P, A, B, C, S = step("P"), step("A"), step("B"), step("C"), step("S")


@pytest.fixture(params=["MockSynchroniser", "TekAWG"])
def make_sync(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return a factory make_sync(**config) for the mock or the AWG (fake VISA connection)."""
    if request.param == "TekAWG":
        rm = MagicMock()
        # spec: VisaObject requires a message-based resource (isinstance check)
        rm.open_resource.return_value = MagicMock(spec=MessageBasedResource)
        # Unknown model, so the configured channels are kept.
        rm.open_resource.return_value.query.return_value = "TEKTRONIX,AWG5204,B0,FV:1.0"
        monkeypatch.setattr(pyvisa, "ResourceManager", MagicMock(return_value=rm))

    def make(**config: Any) -> Any:
        if request.param == "TekAWG":
            config = {"address": "TCPIP::x::INSTR", **config}
        return Synchroniser.create(request.param, config, {})

    return make


def region(sync: Any, steps: Any, config: Any = None, **repeated_child: Any) -> Any:
    """Validate ``steps`` with the synchroniser's configured AWG limits."""
    return validate_repeated_child_region(
        steps,
        repeated_child if config is None else config,
        max_sequence_steps=sync.awg_max_sequence_steps,
        max_step_repeat=sync.awg_max_step_repeat,
        device_type=sync.device_type,
    )


# --- valid layouts ---------------------------------------------------------------


def test_prefix_child_repetitions_suffix(make_sync: Any) -> None:
    steps = [P, A, B, A, B, A, B, S]
    assert region(make_sync(), steps, prefix_steps=1, child_steps=2, repetitions=3) == (
        [P],
        [A, B],
        3,
        [S],
    )


def test_repetitions_are_inferred_when_not_given(make_sync: Any) -> None:
    steps = [P, A, B, A, B, A, B, S]
    assert region(make_sync(), steps, prefix_steps=1, child_steps=2) == (
        [P],
        [A, B],
        3,
        [S],
    )


def test_prefix_defaults_to_zero(make_sync: Any) -> None:
    assert region(make_sync(), [A, A, B], child_steps=1) == ([], [A], 2, [B])


def test_inference_stops_at_first_different_block(make_sync: Any) -> None:
    # The third A after B does not count: repetitions must be contiguous.
    assert region(make_sync(), [A, A, A, B, A], child_steps=1) == ([], [A], 3, [B, A])


def test_explicit_repetitions_may_leave_identical_blocks_in_suffix(
    make_sync: Any,
) -> None:
    assert region(make_sync(), [A, A, A], child_steps=1, repetitions=2) == (
        [],
        [A],
        2,
        [A],
    )


# --- invalid configuration -------------------------------------------------------


def test_config_must_be_a_mapping(make_sync: Any) -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        region(make_sync(), [A, A], config=[1, 2])


def test_child_steps_is_required(make_sync: Any) -> None:
    with pytest.raises(ValueError, match="requires child_steps"):
        region(make_sync(), [A, A], prefix_steps=0)


@pytest.mark.parametrize(
    "config, message",
    [
        ({"prefix_steps": -1, "child_steps": 1}, "prefix_steps must be >= 0"),
        ({"child_steps": 0}, "child_steps must be > 0"),
        ({"child_steps": 1, "repetitions": 0}, "repetitions must be > 0"),
    ],
)
def test_out_of_range_values(make_sync: Any, config: Any, message: Any) -> None:
    with pytest.raises(ValueError, match=message):
        region(make_sync(), [A, A, A], **config)


def test_prefix_plus_child_longer_than_sequence(make_sync: Any) -> None:
    with pytest.raises(
        ValueError, match="exceeds the logical sequence length: 2 \\+ 2 > 3"
    ):
        region(make_sync(), [P, P, A], prefix_steps=2, child_steps=2)


def test_explicit_repetitions_longer_than_sequence(make_sync: Any) -> None:
    with pytest.raises(
        ValueError, match="does not match the expanded logical sequence"
    ) as exc_info:
        region(make_sync(), [A, A, A], child_steps=1, repetitions=4)
    assert "at most 3 complete child repetitions" in str(exc_info.value)


# --- mismatching blocks ------------------------------------------------------------


def test_different_block_inside_repetitions(make_sync: Any) -> None:
    with pytest.raises(
        ValueError, match="Repeated child block mismatch at repetition 2"
    ):
        region(make_sync(), [A, B, A, C], child_steps=2, repetitions=2)


def test_repeat_count_is_part_of_the_block(make_sync: Any) -> None:
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        region(make_sync(), [step("A", 1), step("A", 2)], child_steps=1, repetitions=2)


def test_flags_are_part_of_the_block(make_sync: Any) -> None:
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        region(
            make_sync(),
            [step("A"), step("A", flags=("LASER",))],
            child_steps=1,
            repetitions=2,
        )


def test_inference_needs_two_identical_blocks(make_sync: Any) -> None:
    with pytest.raises(ValueError, match="at least two identical child occurrences"):
        region(make_sync(), [A, B, C], child_steps=1)


# --- hardware limits ---------------------------------------------------------------


def test_child_longer_than_max_sequence_steps(make_sync: Any) -> None:
    sync = make_sync(awg_max_sequence_steps=2)
    with pytest.raises(ValueError, match="Repeated child sequence has 3 steps"):
        region(sync, [A, B, C] * 2, child_steps=3)


def test_compact_body_longer_than_max_sequence_steps(make_sync: Any) -> None:
    # prefix (2) + one child call (1) + suffix (1) = 4 > 3
    sync = make_sync(awg_max_sequence_steps=3)
    with pytest.raises(
        ValueError, match="Compact AWG parent/body sequence has 4 steps"
    ):
        region(sync, [P, P, A, A, S], prefix_steps=2, child_steps=1)


def test_repetitions_above_max_step_repeat(make_sync: Any) -> None:
    sync = make_sync(awg_max_step_repeat=2)
    with pytest.raises(ValueError, match="repeat count 3 exceeds"):
        region(sync, [A, A, A], child_steps=1)


@pytest.mark.parametrize(
    "repeat, message",
    [
        (0, "repeat count must be positive"),
        (-1, "repeat count must be positive"),
        (6, "repeat count 6 exceeds the configured AWG limit of 5"),
    ],
)
def test_validate_awg_repeat_count_rejects(
    make_sync: Any, repeat: Any, message: Any
) -> None:
    sync = make_sync(awg_max_step_repeat=5)
    with pytest.raises(ValueError, match=message):
        validate_awg_repeat_count(
            repeat, sync.awg_max_step_repeat, "step 1", sync.device_type
        )


@pytest.mark.parametrize("repeat", [1, 5])
def test_validate_awg_repeat_count_accepts(make_sync: Any, repeat: Any) -> None:
    # Passes if it does not raise; the boundary value 5 is the configured limit.
    sync = make_sync(awg_max_step_repeat=5)
    validate_awg_repeat_count(
        repeat, sync.awg_max_step_repeat, "step 1", sync.device_type
    )


# --- MockGenerator dry run through load_sequence --------------------------------------


def write_sequence(tmp_path: Path, **extra: Any) -> Any:
    sequence = {
        "total_duration": 1.0,
        "sequencing_order": ["P", "A", "A", "A", "S"],
        "sequencing_repeats": [1, 1, 1, 1, 1],
        # Every block in sequencing_order must be defined (may be empty).
        "P": {},
        "A": {},
        "S": {},
        **extra,
    }
    path = tmp_path / "sequence_0.yaml"
    path.write_text(yaml.safe_dump(sequence))
    return path


@pytest.fixture
def mock() -> Any:
    return Synchroniser.create("MockSynchroniser", {}, {})


def test_mock_load_sequence_flat(mock: Any, tmp_path: Path) -> None:
    mock.load_sequence(write_sequence(tmp_path))
    assert mock.total_duration == 1000


def test_mock_load_sequence_valid_repeated_child(mock: Any, tmp_path: Path) -> None:
    mock.load_sequence(
        write_sequence(
            tmp_path,
            awg_sequence_mode="repeated_child",
            awg_repeated_child={"prefix_steps": 1, "child_steps": 1},
        )
    )
    assert mock.total_duration == 1000


def test_mock_load_sequence_invalid_repeated_child(mock: Any, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        mock.load_sequence(
            write_sequence(
                tmp_path,
                awg_sequence_mode="repeated_child",
                awg_repeated_child={
                    "prefix_steps": 0,
                    "child_steps": 1,
                    "repetitions": 2,
                },
            )
        )


def test_mock_load_sequence_unknown_mode(mock: Any, tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match="awg_sequence_mode: nonsense is not supported"
    ):
        mock.load_sequence(write_sequence(tmp_path, awg_sequence_mode="nonsense"))


def test_mock_dry_run_leaves_sequence_options_unchanged(
    mock: Any, tmp_path: Path
) -> None:
    mock.sequence_options = {"marker": True}
    mock.load_sequence(
        write_sequence(
            tmp_path,
            awg_sequence_mode="repeated_child",
            awg_repeated_child={"prefix_steps": 1, "child_steps": 1},
        )
    )
    assert mock.sequence_options == {"marker": True}
