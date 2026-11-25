from unittest.mock import MagicMock

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from qupyt.hardware.sensor import Sensor
from qupyt.measurement_logic.data_handling import (
    AveragingMode,
    DataConfig,
    MeasurementData,
    MeasurementShape,
    load_measurement,
)
from qupyt.measurement_logic.run_measurement import run_measurement


def make_cam(number_measurements=10, roi=(10, 10)):
    cam = Sensor.create(
        "MockCam", {"number_measurements": number_measurements, "image_roi": list(roi)}
    )
    # Bypass the ROI parsing of the mock sensor to test arbitrary ROIs.
    cam.roi_shape = list(roi)
    return cam


def make_data(params=None, cam=None, **config):
    config = DataConfig(**{"averaging_mode": "spread", **config})
    shape = MeasurementShape.from_params(params or {}, cam or make_cam(), config)
    return MeasurementData(shape, config)


@pytest.mark.parametrize("reference_channels", [1, 2, 10])
@pytest.mark.parametrize("roi", [(10, 10), (1,), (13,), (2, 10, 3, 2)])
def test_spread_shape(reference_channels, roi):
    data = make_data(
        {"dynamic_steps": 6, "pulse_sequence_steps": 3},
        make_cam(10, roi),
        reference_channels=reference_channels,
    )
    assert data.shape.array_shape == (
        reference_channels,
        3,
        6,
        10 // reference_channels,
        *roi,
    )
    assert data.mean().shape == data.shape.array_shape
    assert len(data.shape.axes) == len(data.shape.array_shape)
    data.add(make_cam(10, roi).acquire_data(), ps_step=2, dynamic_step=5)


@pytest.mark.parametrize("roi", [(10, 10), (1,), (2, 10, 3, 2)])
def test_sum_and_live_compression_shapes(roi):
    assert make_data(cam=make_cam(10, roi), averaging_mode="sum").shape.array_shape == (
        2,
        1,
        1,
        1,
        *roi,
    )
    compressed = make_data(cam=make_cam(10, roi), live_compression=True)
    assert compressed.shape.array_shape == (2, 1, 1, 5, 1)
    compressed.add(make_cam(10, roi).acquire_data(), ps_step=0, dynamic_step=0)


@pytest.mark.parametrize("mode", ["sum", "spread"])
@pytest.mark.parametrize("reference_channels", [3, 4])
def test_measurements_must_divide_across_channels(mode, reference_channels):
    with pytest.raises(ValueError, match="divisible"):
        make_data(averaging_mode=mode, reference_channels=reference_channels)


@pytest.mark.parametrize("key", ["dynamic_steps", "pulse_sequence_steps"])
def test_refuse_zero_steps(key):
    with pytest.raises(ValueError):
        make_data({key: 0})


def test_config_validation():
    with pytest.raises(ValidationError):
        DataConfig(averaging_mode="avg")
    with pytest.raises(ValidationError):
        DataConfig(averaging_mode="sum", bogus=1)
    assert DataConfig(averaging_mode="sum").averaging_mode is AveragingMode.SUM


def test_frames_are_split_by_channel_and_averaged():
    data = make_data(cam=make_cam(4, (1,)))
    frames = np.array([[1], [2], [3], [4]])
    data.add(frames, ps_step=0, dynamic_step=0)
    data.add(frames + 10, ps_step=0, dynamic_step=0)
    # channel 0 = frames 0, 2; channel 1 = frames 1, 3; mean of the two averages
    np.testing.assert_array_equal(data.mean()[:, 0, 0, :, 0], [[6, 8], [7, 9]])
    assert data.averages.tolist() == [[2]]


def test_sum_does_not_overflow_small_sensor_dtype():
    data = make_data(cam=make_cam(10, (1,)), averaging_mode="sum")
    frames = np.full((10, 1), 60000, dtype=np.uint16)
    data.add(frames, ps_step=0, dynamic_step=0)
    assert data.mean()[0, 0, 0, 0, 0] == 5 * 60000


def test_unrecorded_slots_are_nan():
    data = make_data({"dynamic_steps": 2}, make_cam(2, (1,)))
    data.add(np.ones((2, 1)), ps_step=0, dynamic_step=0)
    mean = data.mean()
    assert not np.isnan(mean[:, 0, 0]).any()
    assert np.isnan(mean[:, 0, 1]).all()


@pytest.mark.parametrize("frames_shape", [(10, 9, 10), (9, 10, 10), (10,)])
def test_wrong_frame_shape_is_rejected(frames_shape):
    with pytest.raises(ValueError):
        make_data().add(np.ones(frames_shape), ps_step=0, dynamic_step=0)


def test_save_and_load_roundtrip(tmp_path):
    data = make_data({"dynamic_steps": 3}, make_cam(4, (2, 2)))
    data.add(np.arange(16).reshape(4, 2, 2), ps_step=0, dynamic_step=1)
    data.save(tmp_path / "m")
    (tmp_path / "m.yaml").write_text(yaml.dump({"experiment_type": "x"}))

    m = load_measurement(tmp_path / "m.npz")
    assert m.axes == (
        "channel",
        "ps_step",
        "dynamic_step",
        "measurement",
        "roi_0",
        "roi_1",
    )
    np.testing.assert_array_equal(m.data, data.mean())
    assert m.averages.tolist() == [[0, 1, 0]]
    assert m.params == {"experiment_type": "x"}
    np.testing.assert_array_equal(
        m.sel(channel=1, dynamic_step=1)[0, 0], [[4, 5], [6, 7]]
    )
    with pytest.raises(KeyError):
        m.sel(frame=0)


def _run(tmp_path, monkeypatch, data_config, **params):
    monkeypatch.chdir(tmp_path)
    dynamic_devices = MagicMock(devices={})
    params = {
        "experiment_type": "test",
        "averages": 2,
        "synchroniser": {"upload": False},
        "data": data_config,
        **params,
    }
    status = run_measurement(
        MagicMock(), dynamic_devices, make_cam(4, (1,)), MagicMock(), params
    )
    return status, params


def test_run_measurement_writes_npz_and_yaml(tmp_path, monkeypatch):
    status, params = _run(
        tmp_path,
        monkeypatch,
        {"averaging_mode": "spread"},
        dynamic_steps=3,
        pulse_sequence_steps=2,
    )
    assert status == "success"
    m = load_measurement(params["filename"])
    assert m.data.shape == (2, 2, 3, 2, 1)
    assert m.params["completed_averages"] == [[2, 2, 2], [2, 2, 2]]
    assert m.params["measurement_status"] == "success"


def test_run_measurement_with_chunks(tmp_path, monkeypatch):
    status, _ = _run(
        tmp_path,
        monkeypatch,
        {"averaging_mode": "sum", "save_in_chunks": 1},
        dynamic_steps=2,
    )
    assert status == "success"
    assert len(list(tmp_path.glob("test_ch-*.npz"))) == 4


@pytest.mark.parametrize(
    "data_config",
    [{"averaging_mode": "sum", "bogus": 1}, {"averaging_mode": "avg"}, {}],
)
def test_run_measurement_bad_config_fails_cleanly(tmp_path, monkeypatch, data_config):
    status, params = _run(tmp_path, monkeypatch, data_config)
    assert status == "failed"
    assert not list(tmp_path.glob("*.npz"))
    assert (
        yaml.safe_load((tmp_path / (params["filename"] + ".yaml")).read_text())[
            "measurement_status"
        ]
        == "failed"
    )
