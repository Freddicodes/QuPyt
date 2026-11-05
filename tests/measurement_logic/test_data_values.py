"""Data: values written by update_data, save with average_count, dtype from the sensor."""
from types import SimpleNamespace

import numpy as np
import pytest

from qupyt.measurement_logic.data_handling import Data


def make_data(averaging_mode, number_measurements=4, roi=(2,), **config):
    data = Data({"averaging_mode": averaging_mode, "dynamic_steps": 1, **config})
    sensor = SimpleNamespace(roi_shape=list(roi), number_measurements=number_measurements)
    data.set_dims_from_sensor(sensor)
    data.create_array()
    return data


def acquisition(number_measurements=4, roi=(2,)):
    # measurement k has the value k (all pixels)
    values = np.arange(number_measurements, dtype=float)
    return values.reshape(-1, *([1] * len(roi))) * np.ones(roi)


def test_sum_adds_the_measurements_of_each_reference_channel():
    data = make_data("sum")
    data.update_data(acquisition(), ps_step=0, dynamic_step=0, avg_step=0)
    # channel 0: measurements 0 + 2, channel 1: 1 + 3
    np.testing.assert_array_equal(data.data[0, 0, 0, 0], [2, 2])
    np.testing.assert_array_equal(data.data[1, 0, 0, 0], [4, 4])


def test_spread_keeps_measurements_separate():
    data = make_data("spread")
    assert data.data.shape == (2, 1, 1, 2, 2)
    data.update_data(acquisition(), ps_step=0, dynamic_step=0, avg_step=0)
    np.testing.assert_array_equal(data.data[0, 0, 0, :, 0], [0, 2])
    np.testing.assert_array_equal(data.data[1, 0, 0, :, 0], [1, 3])


def test_averages_are_added():
    data = make_data("sum")
    for avg in range(3):
        data.update_data(acquisition(), ps_step=0, dynamic_step=0, avg_step=avg)
    np.testing.assert_array_equal(data.data[0, 0, 0, 0], [6, 6])


def test_live_compression_keeps_one_value_per_channel():
    data = make_data("sum", roi=(2, 3), live_compression=True)
    assert data.data.shape == (2, 1, 1, 1, 1)
    data.update_data(acquisition(roi=(2, 3)), ps_step=0, dynamic_step=0, avg_step=0)
    np.testing.assert_allclose(data.data[:, 0, 0, 0, 0], [2, 4])


@pytest.mark.parametrize(("average_count", "expected"), [(None, 6.0), (0, 6.0), (3, 2.0)])
def test_save_divides_by_average_count(tmp_path, average_count, expected):
    data = make_data("sum")
    data.data.fill(6.0)
    data.save(tmp_path / "out.npy", average_count=average_count)
    np.testing.assert_allclose(np.load(tmp_path / "out.npy"), expected)


def test_dtype_comes_from_the_sensor():
    data = Data({"averaging_mode": "sum", "dynamic_steps": 1})
    sensor = SimpleNamespace(roi_shape=[2], number_measurements=2, target_data_type="uint16")
    data.set_dims_from_sensor(sensor)
    data.create_array()
    assert data.data.dtype == np.uint16


def test_unknown_averaging_mode_raises():
    data = Data({"averaging_mode": "median", "dynamic_steps": 1})
    data.set_dims_from_sensor(SimpleNamespace(roi_shape=[2], number_measurements=2))
    with pytest.raises(ValueError, match="averaging_mode"):
        data.create_array()
