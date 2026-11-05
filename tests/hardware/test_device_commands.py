"""
Commands the device setters send (fake VISA resource / serial port / windfreak package).

Locks in the exact strings, so changes to the setter decorators or the
command sets show up here.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest
import serial
from pyvisa.resources import MessageBasedResource

from qupyt.hardware import visa_handler
from qupyt.hardware.device import Device
from qupyt.mixins import ConfigurationError


@pytest.fixture
def visa(monkeypatch):
    rm = MagicMock()
    instrument = MagicMock(spec=MessageBasedResource)
    instrument.query.return_value = "1"  # OPC done
    rm.open_resource.return_value = instrument
    monkeypatch.setattr(visa_handler.pyvisa, "ResourceManager", MagicMock(return_value=rm))
    monkeypatch.setattr(visa_handler, "sleep", lambda _: None)
    return instrument


@pytest.fixture
def port(monkeypatch):
    port = MagicMock()
    monkeypatch.setattr(serial, "Serial", MagicMock(return_value=port))
    return port


def writes(instrument):
    return [c.args[0] for c in instrument.write.call_args_list]


def apply(device_type, config):
    device = Device.create(device_type, "addr", config)
    device.set_values()
    return device


# --- VISA ---------------------------------------------------------------------------

def test_srs_frequency_and_amplitude(visa):
    apply("SRS", {"frequency": "2.87e9", "amplitude": -10})
    assert writes(visa) == ["FREQ 2870000000.0", "AMPR -10"]


def test_smb_channel_pair_and_list(visa):
    apply("SMB", {"frequency": ("channel_1", 1e9)})
    assert writes(visa) == ["FREQ 1000000000.0"]


def test_tek_afg_two_channels_and_phase(visa):
    apply("TekAFG", {"amplitude": [("channel_1", 0.5), ("channel_2", 1)], "phase": 90})
    assert writes(visa) == [
        "SOURce1:VOLTage:LEVel:IMMediate:AMPLitude 0.5",
        "SOURce2:VOLTage:LEVel:IMMediate:AMPLitude 1",
        "SOURce1:PHASe:ADJust 90 DEG",
    ]


@pytest.mark.parametrize("phase", [400, "abc"])
def test_tek_afg_rejects_invalid_phase(visa, phase):
    with pytest.raises(ConfigurationError):
        apply("TekAFG", {"phase": phase})


def test_tek_afg_gating(visa):
    apply("TekAFG", {"gating": "Gate"})
    assert writes(visa) == ["SOURce1:BURSt:STATe ON", "SOURce1:BURSt:MODE GATed"]


@pytest.mark.parametrize("mode", ["burst", 3])
def test_tek_afg_rejects_invalid_gating(visa, mode):
    with pytest.raises(ConfigurationError):
        apply("TekAFG", {"gating": mode})


def test_rigol_triggered_n_cycles(visa):
    apply("Rigol", {"triggered_n_cycles": ("channel_2", "43")})
    assert writes(visa) == [
        "SOUR2:BURS:STAT ON",
        "SOUR2:BURS:MODE TRIG",
        "SOUR2:BURS:NCYC 43",
    ]


# --- WindFreak over serial ------------------------------------------------------------

def serial_writes(port):
    return [c.args[0] for c in port.write.call_args_list]


def test_windfreak_snv_commands(port):
    apply("WindFreakSNV", {"frequency": "2.87e9", "amplitude": 30, "output_on_off": 1})
    # h1 is sent on creation (high power)
    assert serial_writes(port) == [b"h1", b"f2870.0", b"a30", b"o1"]


def test_windfreak_hdm_selects_the_channel(port):
    apply("WindFreakHDM", {"frequency": ("channel_1", 1e9)})
    assert serial_writes(port) == [b"h1", b"C1", b"f1000.0"]


def test_windfreak_shd_mini_commands(port):
    apply("WindFreakSHDMini", {"amplitude": "5", "output_on_off": 0})
    assert serial_writes(port) == [b"h1", b"W5.0", b"E0"]


# --- WindFreak via the windfreak package ------------------------------------------------

def test_windfreak_official_sets_channel_attributes(monkeypatch):
    channels = [MagicMock(), MagicMock()]
    synth = MagicMock()
    synth.__getitem__.side_effect = lambda i: channels[i]
    fake = types.ModuleType("windfreak")
    fake.SynthHD = MagicMock(return_value=synth)
    monkeypatch.setitem(sys.modules, "windfreak", fake)

    apply("WindFreak", {"frequency": [("channel_0", 1e9), ("channel_1", "2e9")], "amplitude": ("channel_1", -3)})
    assert channels[0].enable is True and channels[1].enable is True  # set on creation
    assert channels[0].frequency == 1e9
    assert channels[1].frequency == 2e9
    assert channels[1].power == -3.0
