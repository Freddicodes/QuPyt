"""
Repeated-child validation (RepeatedChildValidationMixin in
qupyt/hardware/synchroniser/_awg_common.py).

MockGenerator and AWGenerator both use the mixin. Every test runs against
both, which checks that each class wires up the mixin (sequence_options,
awg_max_sequence_steps, awg_max_step_repeat) the same way.
"""
from unittest.mock import MagicMock

from pyvisa.resources import MessageBasedResource

import pytest
import yaml

from qupyt.hardware import visa_handler
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.hardware.synchroniser._awg_common import AWGSequenceStep


def step(name: str, repeat: int = 1, flags: tuple[str, ...] = ()) -> AWGSequenceStep:
    return AWGSequenceStep(name, repeat, flags)


P, A, B, C, S = step("P"), step("A"), step("B"), step("C"), step("S")


@pytest.fixture(params=["MockSynchroniser", "TekAWG"])
def make_sync(request, monkeypatch):
    """Factory: make_sync(**config) creates the mock or the AWG (with a fake VISA connection)."""
    if request.param == "TekAWG":
        rm = MagicMock()
        # spec: VisaObject requires a message-based resource (isinstance check)
        rm.open_resource.return_value = MagicMock(spec=MessageBasedResource)
        # Unknown model, so the configured channels are kept.
        rm.open_resource.return_value.query.return_value = "TEKTRONIX,AWG5204,B0,FV:1.0"
        monkeypatch.setattr(visa_handler.pyvisa, "ResourceManager", MagicMock(return_value=rm))

    def make(**config):
        if request.param == "TekAWG":
            config = {"address": "TCPIP::x::INSTR", **config}
        return Synchroniser.create(request.param, config, {})

    return make


def region(sync, steps, **repeated_child):
    sync.sequence_options = {
        "awg_sequence_mode": "repeated_child",
        "awg_repeated_child": repeated_child,
    }
    return sync._validate_repeated_child_region(steps)


# --- valid layouts ---------------------------------------------------------------

def test_prefix_child_repetitions_suffix(make_sync):
    steps = [P, A, B, A, B, A, B, S]
    assert region(make_sync(), steps, prefix_steps=1, child_steps=2, repetitions=3) == (
        [P], [A, B], 3, [S]
    )


def test_repetitions_are_inferred_when_not_given(make_sync):
    steps = [P, A, B, A, B, A, B, S]
    assert region(make_sync(), steps, prefix_steps=1, child_steps=2) == (
        [P], [A, B], 3, [S]
    )


def test_prefix_defaults_to_zero(make_sync):
    assert region(make_sync(), [A, A, B], child_steps=1) == ([], [A], 2, [B])


def test_inference_stops_at_first_different_block(make_sync):
    # The third A after B does not count: repetitions must be contiguous.
    assert region(make_sync(), [A, A, A, B, A], child_steps=1) == ([], [A], 3, [B, A])


def test_explicit_repetitions_may_leave_identical_blocks_in_suffix(make_sync):
    assert region(make_sync(), [A, A, A], child_steps=1, repetitions=2) == ([], [A], 2, [A])


# --- invalid configuration -------------------------------------------------------

def test_config_must_be_a_mapping(make_sync):
    sync = make_sync()
    sync.sequence_options = {"awg_repeated_child": [1, 2]}
    with pytest.raises(ValueError, match="must be a mapping"):
        sync._validate_repeated_child_region([A, A])


def test_child_steps_is_required(make_sync):
    with pytest.raises(ValueError, match="requires child_steps"):
        region(make_sync(), [A, A], prefix_steps=0)


@pytest.mark.parametrize("config, message", [
    ({"prefix_steps": -1, "child_steps": 1}, "prefix_steps must be >= 0"),
    ({"child_steps": 0}, "child_steps must be > 0"),
    ({"child_steps": 1, "repetitions": 0}, "repetitions must be > 0"),
])
def test_out_of_range_values(make_sync, config, message):
    with pytest.raises(ValueError, match=message):
        region(make_sync(), [A, A, A], **config)


def test_prefix_plus_child_longer_than_sequence(make_sync):
    with pytest.raises(ValueError, match="exceeds the logical sequence length: 2 \\+ 2 > 3"):
        region(make_sync(), [P, P, A], prefix_steps=2, child_steps=2)


def test_explicit_repetitions_longer_than_sequence(make_sync):
    with pytest.raises(ValueError, match="does not match the expanded logical sequence") as exc_info:
        region(make_sync(), [A, A, A], child_steps=1, repetitions=4)
    assert "at most 3 complete child repetitions" in str(exc_info.value)


# --- mismatching blocks ------------------------------------------------------------

def test_different_block_inside_repetitions(make_sync):
    with pytest.raises(ValueError, match="Repeated child block mismatch at repetition 2"):
        region(make_sync(), [A, B, A, C], child_steps=2, repetitions=2)


def test_repeat_count_is_part_of_the_block(make_sync):
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        region(make_sync(), [step("A", 1), step("A", 2)], child_steps=1, repetitions=2)


def test_flags_are_part_of_the_block(make_sync):
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        region(make_sync(), [step("A"), step("A", flags=("LASER",))], child_steps=1, repetitions=2)


def test_inference_needs_two_identical_blocks(make_sync):
    with pytest.raises(ValueError, match="at least two identical child occurrences"):
        region(make_sync(), [A, B, C], child_steps=1)


# --- hardware limits ---------------------------------------------------------------

def test_child_longer_than_max_sequence_steps(make_sync):
    sync = make_sync(awg_max_sequence_steps=2)
    with pytest.raises(ValueError, match="Repeated child sequence has 3 steps"):
        region(sync, [A, B, C] * 2, child_steps=3)


def test_compact_body_longer_than_max_sequence_steps(make_sync):
    # prefix (2) + one child call (1) + suffix (1) = 4 > 3
    sync = make_sync(awg_max_sequence_steps=3)
    with pytest.raises(ValueError, match="Compact AWG parent/body sequence has 4 steps"):
        region(sync, [P, P, A, A, S], prefix_steps=2, child_steps=1)


def test_repetitions_above_max_step_repeat(make_sync):
    sync = make_sync(awg_max_step_repeat=2)
    with pytest.raises(ValueError, match="repeat count 3 exceeds"):
        region(sync, [A, A, A], child_steps=1)


@pytest.mark.parametrize("repeat, message", [
    (0, "repeat count must be positive"),
    (-1, "repeat count must be positive"),
    (6, "repeat count 6 exceeds the configured AWG limit of 5"),
])
def test_validate_awg_repeat_count_rejects(make_sync, repeat, message):
    sync = make_sync(awg_max_step_repeat=5)
    with pytest.raises(ValueError, match=message):
        sync._validate_awg_repeat_count(repeat, "step 1")


@pytest.mark.parametrize("repeat", [1, 5])
def test_validate_awg_repeat_count_accepts(make_sync, repeat):
    # Passes if it does not raise; the boundary value 5 is the configured limit.
    make_sync(awg_max_step_repeat=5)._validate_awg_repeat_count(repeat, "step 1")


# --- MockGenerator dry run through load_sequence --------------------------------------

def write_sequence(tmp_path, **extra):
    sequence = {
        "total_duration": 1.0,
        "sequencing_order": ["P", "A", "A", "A", "S"],
        "sequencing_repeats": [1, 1, 1, 1, 1],
        **extra,
    }
    path = tmp_path / "sequence_0.yaml"
    path.write_text(yaml.safe_dump(sequence))
    return path


@pytest.fixture
def mock():
    return Synchroniser.create("MockSynchroniser", {}, {})


def test_mock_load_sequence_flat(mock, tmp_path):
    mock.load_sequence(write_sequence(tmp_path))
    assert mock.total_duration == 1000


def test_mock_load_sequence_valid_repeated_child(mock, tmp_path):
    mock.load_sequence(write_sequence(
        tmp_path,
        awg_sequence_mode="repeated_child",
        awg_repeated_child={"prefix_steps": 1, "child_steps": 1},
    ))
    assert mock.total_duration == 1000


def test_mock_load_sequence_invalid_repeated_child(mock, tmp_path):
    with pytest.raises(ValueError, match="mismatch at repetition 2"):
        mock.load_sequence(write_sequence(
            tmp_path,
            awg_sequence_mode="repeated_child",
            awg_repeated_child={"prefix_steps": 0, "child_steps": 1, "repetitions": 2},
        ))


def test_mock_load_sequence_unknown_mode(mock, tmp_path):
    with pytest.raises(ValueError, match="awg_sequence_mode: nonsense is not supported"):
        mock.load_sequence(write_sequence(tmp_path, awg_sequence_mode="nonsense"))


def test_mock_dry_run_restores_sequence_options(mock, tmp_path):
    mock.sequence_options = {"marker": True}
    mock.load_sequence(write_sequence(
        tmp_path,
        awg_sequence_mode="repeated_child",
        awg_repeated_child={"prefix_steps": 1, "child_steps": 1},
    ))
    assert mock.sequence_options == {"marker": True}
