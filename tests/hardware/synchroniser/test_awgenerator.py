"""AWGenerator channel detection with a fake pyvisa ResourceManager."""

import logging
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
import pyvisa
from pyvisa.constants import VI_ERROR_TMO
from pyvisa.errors import VisaIOError
from pyvisa.resources import MessageBasedResource

from qupyt.hardware.synchroniser import Synchroniser
from qupyt.hardware.synchroniser.awgenerator import AWGenerator

ADDRESS = "TCPIP::1.2.3.4::INSTR"


@pytest.fixture
def instrument(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return the instrument of the fake ResourceManager (set .query per test)."""
    rm = MagicMock()
    # spec: VisaObject requires a message-based resource (isinstance check)
    rm.open_resource.return_value = MagicMock(spec=MessageBasedResource)
    monkeypatch.setattr(pyvisa, "ResourceManager", MagicMock(return_value=rm))
    return rm.open_resource.return_value


def create_awg(**config: Any) -> Any:
    return Synchroniser.create("TekAWG", {"address": ADDRESS, **config}, {})


def test_create_returns_awgenerator(instrument: Any) -> None:
    instrument.query.return_value = "TEKTRONIX,AWG70002B,B0,FV:1.0"
    awg = create_awg()
    assert isinstance(awg, AWGenerator)
    assert awg.instance is instrument


@pytest.mark.parametrize(
    "identity, expected",
    [
        ("TEKTRONIX,AWG70001B,B0,FV:1.0", [1]),
        ("TEKTRONIX,AWG70002B,B0,FV:1.0", [1, 2]),
        (
            "TEKTRONIX,AWG5204,B0,FV:1.0",
            [1, 2],
        ),  # unknown model: keep configured channels
    ],
)
def test_channels_are_limited_to_detected_outputs(
    instrument: Any, identity: Any, expected: Any
) -> None:
    instrument.query.return_value = identity
    assert create_awg().channels == expected


def test_configured_channel_not_on_device_raises(instrument: Any) -> None:
    instrument.query.return_value = "TEKTRONIX,AWG70001B,B0,FV:1.0"
    with pytest.raises(ValueError, match="do not match the detected"):
        create_awg(channels=[2])


def test_identity_query_failure_raises_and_closes(
    instrument: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # Without the identity, channels and the AWG5202 guard cannot be checked.
    instrument.query.side_effect = VisaIOError(VI_ERROR_TMO)
    with caplog.at_level(logging.WARNING), pytest.raises(VisaIOError):
        create_awg()
    assert "Could not query AWG identity" in caplog.text
    instrument.close.assert_called_once_with()


def test_non_visa_error_in_identity_query_is_not_swallowed(instrument: Any) -> None:
    instrument.query.side_effect = AttributeError("broken")
    with pytest.raises(AttributeError, match="broken"):
        create_awg()


# --- error queue and waveform upload -------------------------------------------


def test_awg_errors_are_read_until_no_error(instrument: Any) -> None:
    awg = create_awg()
    instrument.query.side_effect = [
        '-222,"Data out of range"',
        '-113,"Undefined header"',
        '0,"No error"',
    ]
    with pytest.raises(
        RuntimeError, match=r"during upload: -222,\"Data out of range\"; -113"
    ):
        awg._raise_on_awg_errors("upload")


def test_no_awg_errors_does_not_raise(instrument: Any) -> None:
    awg = create_awg()
    instrument.query.side_effect = ['0,"No error"']
    awg._raise_on_awg_errors("upload")


def test_unparsable_error_response_is_reported(instrument: Any) -> None:
    awg = create_awg()
    instrument.query.side_effect = ["garbage"]
    with pytest.raises(RuntimeError, match="garbage"):
        awg._raise_on_awg_errors("upload")


def test_upload_waveform_commands(instrument: Any) -> None:
    awg = create_awg()
    instrument.query.side_effect = None
    instrument.query.return_value = "1"  # OPC
    waveform = np.array([[0.5, -0.5, 0.0], [128, 0, 16]])
    awg._upload_waveform("block_0_1", waveform)

    assert [c.args[0] for c in instrument.write.call_args_list] == [
        'wlist:waveform:delete "block_0_1"',
        'wlist:waveform:new "block_0_1",3,real',
    ]
    analog_call, marker_call = instrument.write_binary_values.call_args_list
    assert analog_call.args[0] == 'wlist:waveform:data "block_0_1",'
    np.testing.assert_array_equal(analog_call.args[1], [0.5, -0.5, 0.0])
    assert marker_call.args[0] == 'wlist:waveform:marker:data "block_0_1",'
    np.testing.assert_array_equal(marker_call.args[1], [128, 0, 16])
    assert marker_call.kwargs == {"datatype": "B"}
