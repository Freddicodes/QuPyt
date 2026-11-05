"""
Device registry: which device_type name creates which class, and with which
constructor arguments. Measurement YAML files rely on these names.
Registration mechanics are tested in test_registry.py, the VISA connection
in test_visa_handler.py and the Mock's configuration in test_signal_mock.py.
All device creation goes through `create()` below.
"""
import sys
import types
from unittest.mock import MagicMock

from pyvisa.resources import MessageBasedResource

import pytest
import serial

from qupyt.hardware import visa_handler
from qupyt.hardware.device import Device
from qupyt.hardware.device.power_supply import KonradKD3000
from qupyt.hardware.device.signal_mock import MockSignalSource
from qupyt.hardware.device.signal_visa import (
    AFGSignalSource,
    RigolSignalSource,
    SMAandSMBVisaSignalSource,
    SRSSignalSource,
    TekAWG,
)
from qupyt.hardware.device.signal_windfreak import (
    WindFreakHDM,
    WindFreakOfficial,
    WindFreakSHDMini,
    WindFreakSNV,
)
from qupyt.mixins import UnknownDeviceError

ADDRESS = "TCPIP::1.2.3.4::INSTR"

# Every registered device_type name and the exact class it creates.
EXPECTED_CLASSES = {
    "KonradKD3000": KonradKD3000,
    "Mock": MockSignalSource,
    "SRS": SRSSignalSource,
    "TekAWG": TekAWG,
    "SMA": SMAandSMBVisaSignalSource,
    "SMB": SMAandSMBVisaSignalSource,
    "Rigol": RigolSignalSource,
    "TekAFG": AFGSignalSource,
    "WindFreak": WindFreakOfficial,
    "WindFreakHDM": WindFreakHDM,
    "WindFreakSHDMini": WindFreakSHDMini,
    "WindFreakSNV": WindFreakSNV,
}


def create(device_type: str, config: dict | None = None, address: str = ADDRESS):
    """The one place that creates devices."""
    return Device.create(device_type, address, config or {})


@pytest.fixture(autouse=True)
def resource_manager(monkeypatch):
    """Fake pyvisa: open_resource returns a MagicMock instrument."""
    rm = MagicMock()
    # spec: VisaObject requires a message-based resource (isinstance check)
    rm.open_resource.return_value = MagicMock(spec=MessageBasedResource)
    monkeypatch.setattr(visa_handler.pyvisa, "ResourceManager", MagicMock(return_value=rm))
    return rm


@pytest.fixture(autouse=True)
def serial_port(monkeypatch):
    """Fake serial.Serial, used by KonradKD3000 and the WindFreak SNV / HDM / SHDMini classes."""
    port = MagicMock()
    monkeypatch.setattr(serial, "Serial", MagicMock(return_value=port))
    return port


@pytest.fixture(autouse=True)
def windfreak_package(monkeypatch):
    """Fake 'windfreak' package for WindFreakOfficial."""
    fake = types.ModuleType("windfreak")
    fake.SynthHD = MagicMock()
    monkeypatch.setitem(sys.modules, "windfreak", fake)
    return fake


# --- which name creates which class ------------------------------------------------

@pytest.mark.parametrize("device_type, expected_cls", EXPECTED_CLASSES.items())
def test_device_type_creates_class(device_type, expected_cls):
    # Exact class, not isinstance: SRS/TekAWG must not get a VISA subclass.
    assert type(create(device_type)) is expected_cls


def test_registry_has_exactly_the_known_names():
    # Exact list: a missing or renamed registration breaks existing YAML files.
    assert Device.available() == sorted(EXPECTED_CLASSES)


@pytest.mark.parametrize("device_type", ["KonradKD3000", "Mock", "SRS", "SMA", "Rigol", "WindFreakHDM", "WindFreak"])
def test_address_is_stored(device_type):
    assert create(device_type, address="ADDR-42").address == "ADDR-42"


# --- VISA devices -----------------------------------------------------------------

@pytest.mark.parametrize("device_type", ["SRS", "TekAWG", "SMA", "SMB", "Rigol", "TekAFG"])
def test_visa_device_gets_its_device_type_as_s_type(resource_manager, device_type):
    device = create(device_type)
    assert device.s_type == device_type
    assert device.instance is resource_manager.open_resource.return_value
    resource_manager.open_resource.assert_called_once_with(ADDRESS)



# --- WindFreak devices -------------------------------------------------------------

@pytest.mark.parametrize("device_type", ["WindFreakHDM", "WindFreakSHDMini", "WindFreakSNV"])
def test_serial_windfreak_opens_the_port(serial_port, device_type):
    device = create(device_type, address="/dev/tty.usbmodem1")
    serial.Serial.assert_called_once_with("/dev/tty.usbmodem1", timeout=1)
    assert device.instance is serial_port


def test_windfreak_official_uses_the_windfreak_package(windfreak_package):
    device = create("WindFreak", address="/dev/tty.usbmodem1")
    windfreak_package.SynthHD.assert_called_once_with("/dev/tty.usbmodem1")
    device.instance.init.assert_called_once_with()


def test_windfreak_without_package_raises_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "windfreak", None)
    with pytest.raises(ImportError, match="needs package 'windfreak'") as exc_info:
        create("WindFreak")
    assert isinstance(exc_info.value.__cause__, ImportError)


# --- unknown names -----------------------------------------------------------------

def test_unknown_device_type_opens_no_connection(resource_manager):
    with pytest.raises(UnknownDeviceError):
        create("NotADevice")
    resource_manager.open_resource.assert_not_called()
