"""PulseBlaster tests without hardware.

The spinapi wrapper is replaced by a fake module, so the tests check which
calls QuPyt sends to the board, not what the board does with them.
"""

import sys
import types
from typing import Any
from unittest.mock import Mock, call

import pytest

import qupyt.hardware.wrappers
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.hardware.synchroniser.pulseblaster import PulseBlaster

SPINAPI = "qupyt.hardware.wrappers.spinapi_adapted"

# Same values as in spinapi_adapted.py.
CONTINUE, STOP, BRANCH = 0, 1, 6
US = 1000.0
PULSE_PROGRAM = 0

FIRST_INSTRUCTION = 7  # what the fake board returns for the first instruction


@pytest.fixture
def spapi(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fake spinapi module; every board call succeeds unless a test changes it."""
    fake: Any = types.ModuleType(SPINAPI)
    fake.Inst = types.SimpleNamespace(CONTINUE=CONTINUE, STOP=STOP, BRANCH=BRANCH)
    fake.us = US
    fake.PULSE_PROGRAM = PULSE_PROGRAM
    fake.pb_inst_pbonly = Mock(side_effect=lambda *args: FIRST_INSTRUCTION)
    for name in (
        "pb_set_debug",
        "pb_init",
        "pb_core_clock",
        "pb_close",
        "pb_start",
        "pb_stop",
        "pb_start_programming",
        "pb_stop_programming",
    ):
        setattr(fake, name, Mock(return_value=0))
    fake.pb_get_error = Mock(return_value="fake board error")

    # `import a.b.c as x` looks at the parent package attribute first and only
    # then at sys.modules, so patch both (the real module may already be loaded).
    monkeypatch.setitem(sys.modules, SPINAPI, fake)
    monkeypatch.setattr(qupyt.hardware.wrappers, "spinapi_adapted", fake, raising=False)
    return fake


@pytest.fixture
def pb(spapi: Any) -> Any:
    return Synchroniser.create("PulseBlaster", {}, {"LASER": 0})


def instructions(spapi: Any) -> Any:
    """Return the pb_inst_pbonly calls as (flags, instruction, data, length) tuples."""
    return [c.args for c in spapi.pb_inst_pbonly.call_args_list]


# --- creation and configuration ----------------------------------------------


def test_create_uses_the_fake_spinapi(pb: Any, spapi: Any) -> None:
    assert isinstance(pb, PulseBlaster)
    assert pb._spapi is spapi


def test_configuration_sets_sampling_rate_and_min_cycles(spapi: Any) -> None:
    pb = Synchroniser.create(
        "PulseBlaster", {"sampling_rate": 250, "min_instr_clk_cycles": 6}, {}
    )
    assert isinstance(pb, PulseBlaster)
    assert pb.samprate == 250
    assert pb.pb_min_instr_clk_cycles == 6


def test_open_initialises_board_and_sets_clock(spapi: Any) -> None:
    pb = Synchroniser.create("PulseBlaster", {"sampling_rate": 250}, {})
    pb.open()
    spapi.pb_init.assert_called_once_with()
    spapi.pb_core_clock.assert_called_once_with(250)


# --- run / trigger / stop / close --------------------------------------------


def test_run_and_trigger_start_the_program(pb: Any, spapi: Any) -> None:
    pb.run()
    pb.trigger()
    assert spapi.pb_start.call_count == 2


def test_stop_and_close(pb: Any, spapi: Any) -> None:
    pb.open()
    pb.stop()
    pb.close()
    spapi.pb_stop.assert_called_once_with()
    spapi.pb_close.assert_called_once_with()


@pytest.mark.parametrize(
    "method, board_call",
    [
        ("open", "pb_init"),
        ("run", "pb_start"),
        ("stop", "pb_stop"),
        ("close", "pb_close"),
    ],
)
def test_negative_status_raises_runtime_error(
    pb: Any, spapi: Any, method: Any, board_call: Any
) -> None:
    if method == "close":
        pb.open()  # close() only talks to an opened board
    getattr(spapi, board_call).return_value = -1
    with pytest.raises(RuntimeError, match="status=-1: fake board error"):
        getattr(pb, method)()


# --- program_pb ----------------------------------------------------------------


def test_program_pb_sends_one_instruction_per_pulse(pb: Any, spapi: Any) -> None:
    pb.program_pb([0b01, 0b10, 0b11], [1.0, 2.0, 3.0])

    assert instructions(spapi) == [
        (0b01, CONTINUE, 0, 1.0 * US),
        (0b10, CONTINUE, 0, 2.0 * US),
        # The last pulse loops back to the first instruction.
        (0b11, BRANCH, FIRST_INSTRUCTION, 3.0 * US),
        # Trailing STOP instruction.
        (0, STOP, 0, pb.pb_min_instr_clk_cycles * US),
    ]


def test_program_pb_wraps_instructions_in_start_and_stop_programming(
    pb: Any, spapi: Any
) -> None:
    manager = Mock()
    manager.attach_mock(spapi.pb_start_programming, "start")
    manager.attach_mock(spapi.pb_inst_pbonly, "inst")
    manager.attach_mock(spapi.pb_stop_programming, "stop")

    pb.program_pb([1, 2], [1.0, 1.0])

    names = [c[0] for c in manager.mock_calls]
    assert names[0] == "start"
    assert names[-1] == "stop"
    assert manager.mock_calls[0] == call.start(PULSE_PROGRAM)


@pytest.mark.parametrize(
    "duration, short_pulse_bits",
    [
        (0.002, 2**21),  # 1 clock period
        (0.004, 2**22),  # 2 clock periods
        (0.006, 2**21 + 2**22),  # 3 clock periods
        (0.008, 2**23),  # 4 clock periods
        (0.0095, 0),  # rounded up to 10 ns, no short-pulse bits
    ],
)
def test_program_pb_short_pulse_flags(
    pb: Any, spapi: Any, duration: Any, short_pulse_bits: Any
) -> None:
    mask = 0b101
    pb.program_pb([mask, 0b001], [duration, 1.0])

    first_flags = instructions(spapi)[0][0]
    assert first_flags == mask + short_pulse_bits


def test_program_pb_rounds_unsupported_short_pulse_with_warning(
    pb: Any, spapi: Any, caplog: pytest.LogCaptureFixture
) -> None:
    pb.program_pb([1, 1], [0.0025, 1.0])
    assert "pulse duration of 0.0025 not possible" in caplog.text


def test_program_pb_rejects_mismatched_lengths(pb: Any, spapi: Any) -> None:
    with pytest.raises(ValueError):
        pb.program_pb([1, 2, 3], [1.0, 1.0])


def test_program_pb_raises_on_failed_instruction(pb: Any, spapi: Any) -> None:
    spapi.pb_inst_pbonly.side_effect = None
    spapi.pb_inst_pbonly.return_value = -1
    with pytest.raises(RuntimeError, match="fake board error"):
        pb.program_pb([1, 2], [1.0, 1.0])
    # Programming mode is always left again, even after a rejected instruction.
    spapi.pb_stop_programming.assert_called_once_with()


def test_close_without_open_does_not_touch_the_board(pb: Any, spapi: Any) -> None:
    pb.close()
    spapi.pb_close.assert_not_called()


def test_open_twice_initialises_once(pb: Any, spapi: Any) -> None:
    pb.open()
    pb.open()
    spapi.pb_init.assert_called_once_with()


@pytest.mark.parametrize("durations", [[], [1.0, -1.0], [1.0, float("nan")]])
def test_program_pb_rejects_invalid_durations(
    pb: Any, spapi: Any, durations: Any
) -> None:
    with pytest.raises(ValueError):
        pb.program_pb([1] * len(durations), durations)
    spapi.pb_start_programming.assert_not_called()
