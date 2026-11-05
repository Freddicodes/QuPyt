"""VisaObject tests with a fake pyvisa ResourceManager."""
from unittest.mock import MagicMock

from pyvisa.resources import MessageBasedResource

import pytest

from qupyt.hardware import visa_handler
from qupyt.hardware.device import signal_visa
from qupyt.hardware.device.signal_visa import (
    AFGSignalSource,
    RigolSignalSource,
    SMAandSMBVisaSignalSource,
    SRSSignalSource,
    TekAWG,
)
from qupyt.hardware.visa_handler import VisaObject

VISA_DEVICE_CLASSES = [
    SRSSignalSource,
    SMAandSMBVisaSignalSource,
    RigolSignalSource,
    AFGSignalSource,
    TekAWG,
]


@pytest.fixture
def resource_manager(monkeypatch):
    """Replace pyvisa.ResourceManager; the opened instrument is a MagicMock."""
    rm = MagicMock()
    # spec: VisaObject requires a message-based resource (isinstance check)
    rm.open_resource.return_value = MagicMock(spec=MessageBasedResource)
    monkeypatch.setattr(visa_handler.pyvisa, "ResourceManager", MagicMock(return_value=rm))
    monkeypatch.setattr(visa_handler, "sleep", lambda _: None)
    return rm


@pytest.fixture
def instrument(resource_manager):
    return resource_manager.open_resource.return_value


# --- command sets ----------------------------------------------------------------

def test_default_command_set_only_has_opc():
    assert VisaObject.COMMANDS == {"OPC": "*OPC?"}


@pytest.mark.parametrize("cls", VISA_DEVICE_CLASSES, ids=lambda c: c.__name__)
def test_every_visa_device_class_has_an_opc_command(cls):
    assert "OPC" in cls.COMMANDS


def test_instance_uses_the_class_command_set(resource_manager):
    srs = SRSSignalSource("TCPIP::x::INSTR", "SRS", {})
    assert srs.command is SRSSignalSource.COMMANDS
    assert srs.command["SetFreq1"] == "FREQ "


def test_smb_and_sma_share_the_command_set(resource_manager):
    smb = SMAandSMBVisaSignalSource("TCPIP::x::INSTR", "SMB", {})
    sma = SMAandSMBVisaSignalSource("TCPIP::x::INSTR", "SMA", {})
    assert smb.command == sma.command
    assert smb.command["SetFreq1"] == "FREQ "
    assert (smb.s_type, sma.s_type) == ("SMB", "SMA")


def test_repr_shows_the_concrete_class(resource_manager):
    srs = SRSSignalSource("TCPIP::x::INSTR", "SRS", {})
    assert repr(srs).startswith("SRSSignalSource(")


def test_tek_awg_close_sleeps_then_closes_the_instrument(resource_manager, instrument, monkeypatch):
    slept = []
    monkeypatch.setattr(signal_visa, "sleep", slept.append)
    TekAWG("TCPIP::x::INSTR", "TekAWG", {}).close()
    assert slept == [5]
    instrument.close.assert_called_once_with()


# --- opening -------------------------------------------------------------------

def test_open_uses_handle_and_sets_timeout(resource_manager, instrument):
    visa = VisaObject("TCPIP::1.2.3.4::INSTR", "SRS")
    resource_manager.open_resource.assert_called_once_with("TCPIP::1.2.3.4::INSTR")
    assert visa.instance is instrument
    assert instrument.timeout == 60000


def test_failed_open_is_reraised_and_cleaned_up(resource_manager):
    resource_manager.open_resource.side_effect = OSError("no route to host")
    with pytest.raises(ConnectionError, match="Could not open SRS at VISA address TCPIP::x::INSTR") as exc_info:
        VisaObject("TCPIP::x::INSTR", "SRS")
    assert str(exc_info.value.__cause__) == "no route to host"
    resource_manager.close.assert_called_once_with()


# --- opc_wait -------------------------------------------------------------------

def test_opc_wait_returns_on_nonzero_response(resource_manager, instrument):
    instrument.query.return_value = "1\n"
    VisaObject("TCPIP::x::INSTR", "SRS").opc_wait()
    instrument.query.assert_called_once_with("*OPC?")


def test_opc_wait_retries_invalid_responses(resource_manager, instrument):
    instrument.query.side_effect = ["garbage", "1"]
    VisaObject("TCPIP::x::INSTR", "SRS").opc_wait()
    assert instrument.query.call_count == 2


def test_opc_wait_gives_up_after_three_attempts(resource_manager, instrument):
    instrument.query.return_value = "0"
    with pytest.raises(TimeoutError, match="did not return a valid OPC response"):
        VisaObject("TCPIP::x::INSTR", "SRS").opc_wait()
    assert instrument.query.call_count == 3


def test_non_message_based_resource_raises_type_error_and_closes(resource_manager):
    from pyvisa.resources import Resource

    resource = MagicMock(spec=Resource)  # e.g. a raw register-based resource
    resource_manager.open_resource.return_value = resource
    with pytest.raises(TypeError, match="not a message-based resource"):
        VisaObject("USB0::1::2::3::RAW", "SRS")
    resource.close.assert_called_once()
    resource_manager.close.assert_called_once()
