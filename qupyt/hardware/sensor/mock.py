from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser import Synchroniser


class MockCam(Sensor, sensor_types=("Mock", "MockCam")):
    """
    Camera without hardware, for testing measurement configurations.
    Returns Poisson noise (λ = 15000) of the configured shape.

    :param configuration: Configuration keys of :class:`Sensor`
        (``number_measurements``) plus ``image_roi``
        (``[height, width, x_offset, y_offset]``; the offsets are ignored).
        Default ROI: 200 × 200.
    :type configuration: dict | None
    """

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        super().__init__(configuration)
        self.roi_shape = [200, 200]
        self.attribute_map["image_roi"] = self._set_roi
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"MockCam(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"MockCam sensor instance: MockCam(configuration: {self.initial_configuration_dict})"

    def _set_roi(self, roi_shape_and_offset: list[int]) -> None:
        """
        This function emulates the current behaviour of the GenICam and Basler camera sensor.
        ROI values are extracted from combined input of the ROI and offset values.
        """
        self.roi_shape = roi_shape_and_offset[:2]
        _ = roi_shape_and_offset[2:]

    def open(self) -> None:
        """
        passes since there is no device to open.
        """

    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """
        Sends a trigger signal to the synchroniser if there is one.
        Returns an array of shape ``[number_measurements, height, width]``
        as specified in configuration. Array contains Poisson distributed values
        with λ = 15000.

        See :meth:`Sensor.acquire_data`.
        """
        if synchroniser is not None:
            synchroniser.trigger()
        noise = np.random.poisson(
            15_000,
            size=(self.number_measurements, *self.roi_shape),
        )
        return noise

    def close(self) -> None:
        """
        Passes since there is no device to close.
        """