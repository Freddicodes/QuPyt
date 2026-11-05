"""Tests for Sensor registry.

the names measurement YAML files use, SDK import errors and the MockCam.
Registration mechanics are tested in test_registry.py.
"""

import sys
from typing import Any

import pytest

from qupyt.hardware.sensor import Sensor

# Exact list (and therefore sorted): a missing or renamed registration
# would break existing measurement YAML files.
EXPECTED_SENSORS = [
    "Basler1920",
    "DAQ",
    "EoSense1.1CXP",
    "HeliC3",
    "Mock",
    "MockCam",
    "PhantomS710",
]


def test_all_sensors_available() -> None:
    assert Sensor.available() == EXPECTED_SENSORS


@pytest.mark.parametrize(
    ("sensor_type", "missing_modules", "message"),
    [
        ("Basler1920", ["pypylon"], r"qupyt\[basler\]"),
        ("EoSense1.1CXP", ["harvesters", "harvesters.core"], r"qupyt\[genicam\]"),
        ("PhantomS710", ["egrabber"], r"egrabber"),
        ("HeliC3", ["libHeLIC"], r"libHeLIC"),
        ("DAQ", ["nidaqmx", "nidaqmx.constants"], r"qupyt\[daq\]"),
    ],
)
def test_missing_sdk_raises_helpful_import_error(
    monkeypatch: pytest.MonkeyPatch,
    sensor_type: Any,
    missing_modules: Any,
    message: Any,
) -> None:
    # A None entry in sys.modules makes any import of that module raise ImportError,
    # even on machines where the SDK is installed, so no hardware is touched.
    for module in missing_modules:
        monkeypatch.setitem(sys.modules, module, None)
    # HeliCam adds its SDK folder to sys.path; restore it after the test.
    monkeypatch.setattr(sys, "path", list(sys.path))

    with pytest.raises(ImportError, match=message):
        Sensor.create(sensor_type, {})


@pytest.mark.parametrize("name", ["Mock", "MockCam"])
def test_mockcam_end_to_end(name: Any) -> None:
    cam = Sensor.create(name, {"number_measurements": 4, "image_roi": [3, 5, 0, 0]})
    cam.open()
    data = cam.acquire_data(None)
    cam.close()

    assert data.shape == (4, 3, 5)
