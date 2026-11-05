"""configuration=None / {} for sensors and synchronisers that index it directly."""

import sys
import types
from typing import Any
from unittest.mock import Mock

import pytest

from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser


@pytest.fixture
def fake_harvesters(monkeypatch: pytest.MonkeyPatch) -> Any:
    core: Any = types.ModuleType("harvesters.core")
    core.Harvester = Mock()
    package: Any = types.ModuleType("harvesters")
    package.core = core
    monkeypatch.setitem(sys.modules, "harvesters", package)
    monkeypatch.setitem(sys.modules, "harvesters.core", core)
    return core


@pytest.mark.parametrize("configuration", [None, {}])
def test_genicam_without_cti_raises_value_error(
    fake_harvesters: Any, configuration: Any
) -> None:
    with pytest.raises(ValueError, match="GenTL_producer_cti"):
        Sensor.create("EoSense1.1CXP", configuration)
    fake_harvesters.Harvester.assert_not_called()


@pytest.fixture
def fake_pulsestreamer(monkeypatch: pytest.MonkeyPatch) -> Any:
    module: Any = types.ModuleType("pulsestreamer")
    module.findPulseStreamers = Mock(return_value=[("192.168.0.7", "serial")])
    monkeypatch.setitem(sys.modules, "pulsestreamer", module)
    return module


@pytest.mark.parametrize(
    "configuration", [None, {}, {"address": "None"}, {"address": None}]
)
def test_pstreamer_without_address_discovers_the_device(
    fake_pulsestreamer: Any, configuration: Any
) -> None:
    ps = Synchroniser.create("SwabInstPS", configuration, {})
    fake_pulsestreamer.findPulseStreamers.assert_called_once()
    assert ps.address == "192.168.0.7"


def test_pstreamer_with_address_does_not_discover(fake_pulsestreamer: Any) -> None:
    ps = Synchroniser.create("SwabInstPS", {"address": "10.0.0.1"}, {})
    fake_pulsestreamer.findPulseStreamers.assert_not_called()
    assert ps.address == "10.0.0.1"
