"""
Power supply tests with a fake serial port.

The tests check which bytes QuPyt writes to the port, not what the hardware
does with them (the OUT1 / OUT0 commands are still to be confirmed).
"""
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from qupyt.hardware.device import Device, power_supply
from qupyt.hardware.device.power_supply import KonradKD3000, PowerSupply
from qupyt.hardware.device_handler import DeviceHandler
from qupyt.mixins import ConfigurationError

PORT = "/dev/tty.usbserial-1"


@pytest.fixture
def port(monkeypatch):
    """The fake serial connection returned by serial.Serial."""
    fake_port = MagicMock()
    monkeypatch.setattr(power_supply.serial, "Serial", MagicMock(return_value=fake_port))
    return fake_port


def create(config: dict[str, Any] | None = None) -> KonradKD3000:
    return Device.create("KonradKD3000", PORT, config or {})


def written(port) -> list[bytes]:
    return [c.args[0] for c in port.write.call_args_list]


# --- creating ------------------------------------------------------------------------

def test_create_opens_the_serial_port(port):
    psu = create()
    assert isinstance(psu, KonradKD3000)
    power_supply.serial.Serial.assert_called_once_with(PORT, timeout=1)
    assert psu.instance is port
    assert psu.address == PORT


def test_creating_does_not_switch_the_output(port):
    create({"output": "on"})
    port.write.assert_not_called()


def test_power_supply_base_is_abstract():
    with pytest.raises(TypeError, match="abstract"):
        PowerSupply({})


def test_repr_and_str_name_the_model(port):
    psu = create()
    assert repr(psu) == f"KonradKD3000(address: {PORT})"
    assert "KonradKD3000" in str(psu)


# --- output via the configuration ----------------------------------------------------

@pytest.mark.parametrize("value, command", [
    ("on", b"OUT1"),
    ("off", b"OUT0"),
    ("ON ", b"OUT1"),   # case and surrounding spaces are ignored
    (" Off", b"OUT0"),
    (True, b"OUT1"),    # YAML: unquoted on / yes / true
    (False, b"OUT0"),   # YAML: unquoted off / no / false
])
def test_output_value_writes_command(port, value, command):
    create({"output": value}).set_values()
    assert written(port) == [command]


def test_missing_output_switches_off(port):
    create({}).set_values()
    assert written(port) == [b"OUT0"]


def test_none_configuration_switches_off(port):
    psu = create()
    psu.update_configuration(None)
    psu.set_values()
    assert written(port) == [b"OUT0"]


@pytest.mark.parametrize("value", ["maybe", "", "1", 1, 0, None, ["on"]])
def test_invalid_output_raises_and_writes_nothing(port, value):
    psu = create({"output": value})
    with pytest.raises(ConfigurationError, match="power supply output"):
        psu.set_values()
    port.write.assert_not_called()


def test_unknown_config_key_raises(port):
    with pytest.raises(KeyError, match="Unknown attribute: voltage"):
        create({"output": "on", "voltage": 5}).set_values()


def test_defaults_are_not_shared_between_calls(port):
    # set_values must not write the merged dict back into DEFAULTS.
    create({"output": "on"}).set_values()
    assert PowerSupply.DEFAULTS == {"output": "off"}


# --- direct methods ---------------------------------------------------------------------

def test_on_and_off_write_commands(port):
    psu = create()
    psu.on()
    psu.off()
    assert written(port) == [b"OUT1", b"OUT0"]


def test_set_output_directly(port):
    psu = create()
    psu.set_output("on")
    assert written(port) == [b"OUT1"]


def test_close_closes_the_port(port):
    psu = create()
    psu.close()
    port.close.assert_called_once_with()


# --- through the DeviceHandler (static device) -----------------------------------------

def entry(config: dict[str, Any]) -> dict[str, Any]:
    return {"device_type": "KonradKD3000", "address": PORT, "config": config}


def test_handler_switches_on_then_off_when_key_is_dropped(port):
    # Measurement 1 switches on; measurement 2 reuses the device without
    # "output", so the default switches it off (also covers the reuse fix).
    handler = DeviceHandler({})
    handler.update_devices({"psu": entry({"output": "on"})})
    handler.set_all_params()
    handler.update_devices({"psu": entry({})})
    handler.set_all_params()

    assert written(port) == [b"OUT1", b"OUT0"]
    power_supply.serial.Serial.assert_called_once()  # reused, not reopened


def test_handler_closes_the_port_when_device_is_removed(port):
    handler = DeviceHandler({})
    handler.update_devices({"psu": entry({"output": "on"})})
    handler.update_devices({})
    assert port.close.call_args_list == [call()]
