from typing import TYPE_CHECKING, Any

import numpy as np

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser.base import Synchroniser


class MockCam(Sensor, sensor_types=("MockCam",)):
    """
    To ensure users can test their code and the general logic without having
    to buy expensive lab equipment, most devices in QuPyt implement
    mocking behoviour. Mock devices do not talk to any real hardware.
    They implement a dummy interface, where API functions either pass,
    sleep or return noise.
    This is the mock variant of a sensor.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dicionary to set
          associated values.

          Possible configuration values:
            - **image_roi** (list[int]): Region Of Interest of the sensor in
              the following format: [height, width, x_offset, y_offset].
              The roi_shape attribute of the :class:`Sensor` base class will
              be derived from this.

          Possible configuration values:

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.
    """

    def __init__(self, configuration: dict[str, Any]) -> None:
        super().__init__(configuration)
        self.roi_shape = [200, 200]
        self.attribute_map["image_roi"] = self._set_roi
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"MockCam(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"MockCam sensor instance: DAQ(configuration: {self.initial_configuration_dict})"

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
        Sends tigger signal to synchroniser if there is one.
        Returns an array of shape ``[number_measrurements, height, witdh]``
        as specified in configuration. Array contains Poisson distributed values
        with k=15000

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
