"""
run_measurement: the measurement loop, saved files and status.

MockCam returns ones (np.random.poisson patched), the synchroniser is a Mock
so the calls can be checked, the workspace lives in tmp_path.
"""
from unittest.mock import Mock, call

import numpy as np
import pytest
import yaml

from qupyt import set_up
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.sensor import mock as mock_sensor
from qupyt.measurement_logic import run_measurement as rm
from qupyt.measurement_logic.run_measurement import _as_bool, run_measurement
from qupyt.set_up import QuPytConfig, set_config


@pytest.fixture(autouse=True)
def workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(set_up, "_config", set_up._config)  # restored afterwards
    config = QuPytConfig(tmp_path / "in", tmp_path / "out")
    config.create_ws()
    set_config(config)
    monkeypatch.setattr(rm, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        mock_sensor.np.random, "poisson", lambda _lam, size: np.ones(size)
    )
    return config


def make_params(**overrides):
    params = {
        "experiment_type": "ODMR",
        "averages": 3,
        "dynamic_steps": 1,
        "data": {"averaging_mode": "sum", "dynamic_steps": 1},
        "synchroniser": {"upload": False},
    }
    params.update(overrides)
    return params


def run(params, synchroniser=None):
    sensor = Sensor.create("MockCam", {"number_measurements": 2, "image_roi": [2, 3, 0, 0]})
    synchroniser = synchroniser or Mock()
    status = run_measurement(
        DeviceHandler({}),
        DynamicDeviceHandler({}, number_dynamic_steps=1),
        sensor,
        synchroniser,
        params,
    )
    return status, synchroniser


def saved_files(directory):
    npy = sorted(directory.glob("ODMR_*.npy"))
    yml = sorted(directory.glob("ODMR_*.yaml"))
    assert len(npy) == 1 and len(yml) == 1
    return np.load(npy[0]), yaml.safe_load(yml[0].read_text(encoding="utf-8"))


# --- success ------------------------------------------------------------------

def test_success_saves_averaged_data_and_params(workspace):
    params = make_params()
    status, _ = run(params)
    assert status == "success"
    data, saved_params = saved_files(workspace.output_dir)
    # (reference_channels, ps_steps, dynamic_steps, 1, *roi); each channel gets
    # one of the two measurements per average; 3 averages / 3 = 1.
    assert data.shape == (2, 1, 1, 1, 2, 3)
    np.testing.assert_allclose(data, 1.0)
    assert saved_params["measurement_status"] == "success"
    assert saved_params["output_dir"] == str(workspace.output_dir)
    assert "qupyt_version" in saved_params


def test_sensor_triggers_synchroniser_once_per_average():
    _, synchroniser = run(make_params(averages=4))
    assert synchroniser.trigger.call_count == 4
    synchroniser.open.assert_called_once()
    synchroniser.close.assert_called_once()


def test_upload_false_skips_load_sequence():
    _, synchroniser = run(make_params(synchroniser={"upload": "false"}))
    synchroniser.load_sequence.assert_not_called()


def test_upload_loads_one_sequence_per_ps_step(workspace):
    params = make_params(
        synchroniser={"upload": True},
        pulse_sequence_steps=2,
        data={"averaging_mode": "sum", "dynamic_steps": 1, "ps_steps": 2},
    )
    _, synchroniser = run(params)
    seq_dir = workspace.seq_dir
    assert synchroniser.load_sequence.call_args_list == [
        call(seq_dir / "sequence_0.yaml"),
        call(seq_dir / "sequence_1.yaml"),
    ]


def test_yaml_output_dir_beats_config(tmp_path):
    target = tmp_path / "from_yaml"
    run(make_params(output_dir=str(target)))
    data, saved_params = saved_files(target)
    assert saved_params["output_dir"] == str(target.resolve())


# --- failure ------------------------------------------------------------------

def test_failure_returns_failed_and_saves_raw_sum(workspace):
    synchroniser = Mock()
    synchroniser.run.side_effect = RuntimeError("board on fire")
    status, _ = run(make_params(), synchroniser)
    assert status == "failed"
    data, saved_params = saved_files(workspace.output_dir)
    assert saved_params["measurement_status"] == "failed"
    np.testing.assert_allclose(data, 0.0)  # failed before the first acquisition
    synchroniser.close.assert_called_once()


# --- _as_bool -----------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", True), (" Yes ", True), ("on", True),
     ("1", True), ("false", False), ("NO", False), ("off", False), ("0", False),
     (1, True), (0, False)],
)
def test_as_bool(value, expected):
    assert _as_bool(value) is expected
