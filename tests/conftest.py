"""Shared fixtures which construct real QuPyt objects and isolate external effects."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import numpy as np
import pytest

from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor.mock import MockCam
from qupyt.hardware.synchroniser.mock import MockGenerator
from tests.support import MeasurementEnvironment, use_workspace

measurement_module = importlib.import_module("qupyt.measurement_logic.run_measurement")


@pytest.fixture
def measurement_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> MeasurementEnvironment:
    monkeypatch.chdir(tmp_path)
    use_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(measurement_module, "sleep", lambda _: None)
    monkeypatch.setattr(measurement_module, "tqdm", lambda iterable, **_: iterable)
    caplog.set_level(logging.INFO)
    sensor = MockCam({"number_measurements": 2, "image_roi": [1]})
    acquisition = Mock(return_value=np.ones((2, 1)))
    monkeypatch.setattr(np.random, "poisson", acquisition)
    sensor_close = Mock(wraps=sensor.close)
    monkeypatch.setattr(sensor, "close", sensor_close)
    synchroniser = MockGenerator({}, {})
    synchroniser_close = Mock(wraps=synchroniser.close)
    monkeypatch.setattr(synchroniser, "close", synchroniser_close)
    params = {
        "experiment_type": "logging_test",
        "averages": 2,
        "dynamic_steps": 2,
        "pulse_sequence_steps": 2,
        "synchroniser": {"upload": False},
        "data": {"averaging_mode": "spread", "dynamic_steps": 2, "ps_steps": 2},
    }
    return MeasurementEnvironment(
        static=DeviceHandler({}),
        dynamic=DynamicDeviceHandler({}, number_dynamic_steps=2),
        sensor=sensor,
        acquisition=acquisition,
        synchroniser=synchroniser,
        params=params,
        directory=tmp_path,
        sensor_close=sensor_close,
        synchroniser_close=synchroniser_close,
    )


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the real ``qupyt.main`` module with a workspace under ``tmp_path``.

    ``main`` no longer configures logging or parses arguments on import, so no
    module reload is needed; output goes to ``tmp_path``.
    """
    from queue import Queue

    import qupyt.main

    monkeypatch.chdir(tmp_path)
    use_workspace(monkeypatch, tmp_path)
    # Tests put instruction paths here; see tests.support.run_worker_until_idle.
    monkeypatch.setattr(qupyt.main, "queue", Queue(), raising=False)
    return qupyt.main


@pytest.fixture
def service_main(
    service: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    """Run ``service.main()`` without real logging setup or watchdog threads."""
    monkeypatch.setattr(service.sys, "argv", ["qupyt"])
    monkeypatch.setenv("QUPYT_IN_DIR", str(tmp_path / ".qupyt"))
    monkeypatch.setenv("QUPYT_OUT_DIR", str(tmp_path))
    # setup_logger would replace pytest's log capture handler.
    monkeypatch.setattr(service, "setup_logger", lambda **_: None)
    monkeypatch.setattr(service, "QUPYT_LOGO", "")
    return service
