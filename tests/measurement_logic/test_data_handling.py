from pathlib import Path
from typing import Any

import numpy as np
import pytest

from qupyt.hardware.sensor import Sensor
from qupyt.measurement_logic.data_handling import Data


# Tests if the number_measurements can be distributed across the reference channels.
# Should pass if number_measurements is divisible by reference_channels.
@pytest.mark.parametrize(
    "reference_channels", [1, 2, 10]
)  # nframes = 10 should be divisible.
def test_set_dims_from_sensor(reference_channels: Any) -> None:
    dynamic_steps = 6
    nframes = 10
    roi = [10, 10]
    cam = Sensor.create("MockCam", {"number_measurements": nframes, "image_roi": roi})
    data = Data(
        {
            "averaging_mode": "spread",
            "dynamic_steps": dynamic_steps,
            "live_compression": False,
            "reference_channels": reference_channels,
        }
    )
    data.set_dims_from_sensor(cam)
    data.create_array()
    assert data.data.shape == (
        reference_channels,
        1,
        dynamic_steps,
        int(nframes / reference_channels),
        *roi,
    )


# Tests if the number_measurements can be distributed across the reference channels.
# Expected to raise ValueError because number_measurements not divisible by reference_channels.
@pytest.mark.parametrize("reference_channels", [3, 4])
def test_set_dims_from_sensor_refuse_dist_measurements_channels(
    reference_channels: Any,
) -> None:
    dynamic_steps = 6
    nframes = 10
    roi = [10, 10]
    cam = Sensor.create("MockCam", {"number_measurements": nframes, "image_roi": roi})
    data = Data(
        {
            "averaging_mode": "spread",
            "dynamic_steps": dynamic_steps,
            "live_compression": False,
            "reference_channels": reference_channels,
        }
    )
    data.set_dims_from_sensor(cam)
    with pytest.raises(ValueError):
        data.create_array()


@pytest.mark.parametrize("live_compression", [False, True])
@pytest.mark.parametrize("reference_channels", [1, 2])
@pytest.mark.parametrize("image_roi", [[10, 10], [1], [13], [2, 10, 3, 2]])
def test_array_shape_for_various_rois(
    live_compression: Any, reference_channels: Any, image_roi: Any
) -> None:
    """Live compression averages over the ROI, leaving a single trailing dimension."""
    dynamic_steps = 6
    nframes = 10
    cam = Sensor.create(
        "MockCam", {"number_measurements": nframes, "image_roi": image_roi}
    )
    # Bypass the roi parsing mechanism of the Mock Sensor to test the behavior of the Data container.
    cam.roi_shape = image_roi
    data = Data(
        {
            "averaging_mode": "spread",
            "dynamic_steps": dynamic_steps,
            "live_compression": live_compression,
            "reference_channels": reference_channels,
        }
    )
    data.set_dims_from_sensor(cam)
    data.create_array()
    expected_roi = [1] if live_compression else image_roi
    assert data.data.shape == (
        reference_channels,
        1,
        dynamic_steps,
        nframes // reference_channels,
        *expected_roi,
    )


# No dimension of the data container can be zero
# => This would reduce the overall size of the array to 0.
def test_data_container_refuse_0_dyn_steps() -> None:
    dynamic_steps = 0
    with pytest.raises(ValueError):
        Data({"averaging_mode": "spread", "dynamic_steps": dynamic_steps})


def test_chunks_are_saved_into_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    data = Data({"averaging_mode": "sum", "dynamic_steps": 1, "save_in_chunks": 1})
    data.number_measurements = 2
    data.roi_shape = [3]
    data.data_type = float
    data.create_array()
    data.set_filename(("ODMR", "stamp"), out_dir)

    data.update_data(np.ones((2, 3)), 0, 0, 0)

    assert (out_dir / "ODMR_ch-0_stamp.npy").is_file()
    assert not list(tmp_path.glob("*.npy"))
