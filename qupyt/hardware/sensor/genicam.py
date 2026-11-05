from __future__ import annotations

import logging
from time import time
from typing import TYPE_CHECKING, Any

import numpy as np

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser import Synchroniser

logger = logging.getLogger(__name__)

class GenICamHarvester(Sensor, sensor_types=("EoSense1.1CXP",)):
    """
    Sensor class implementation for all simple GenICam compliant cameras.
    This class relies on the excellent
    `harvesters <https://github.com/genicam/harvesters>`_ library.
    You will need to provide your own GenTL producer file for this to work.
    Check the recommendations `here <https://github.com/genicam/harvesters>`_
    or get it from your GenICam compliant camera manufacturer.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dictionary to set
          associated values.

          Possible configuration values:
            - **exposure_time** (int, µs)
            - **image_roi** (list[int]): Region Of Interest of the sensor in
              the following format: [height, width, x_offset, y_offset].
              The roi_shape attribute of the :class:`Sensor` base class will
              be derived from this.
            - **gain** (number): written to the camera's ``Gain`` node as is
              (units depend on the camera).
            - **GenTL_producer_cti** (string, required): Path to the GenTL
              producer (cti) file on your computer.

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.

    Note:
        Currently this class preconfigures the following:
          - **CXP link configuration**: CXP12_X4. This means the frame grabber expects
            to communicate via CoaXPress 12 on 4 lanes.
          - **pixel_format**: Mono10
          - **trigger**: ``TriggerMode`` On, ``TriggerSource`` Line0,
            ``TriggerSelector`` FrameStart (one frame per hardware trigger)
          - **defaults** before the configuration is applied: exposure 20 µs,
            ROI 800 × 1200 at offset (0, 0)

    Raises (__init__):
        - **ValueError** if ``GenTL_producer_cti`` is missing from the configuration
        - **FileNotFoundError** if the .cti file cannot be loaded
    """

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        try:
            from harvesters.core import Harvester
        except ImportError as e:
            raise ImportError(
                "GenICamHarvester needs package 'harvesters'. Install with pip install qupyt[genicam]"
            ) from e

        if not configuration or "GenTL_producer_cti" not in configuration:
            raise ValueError(
                "GenICamHarvester needs 'GenTL_producer_cti' (path to the .cti file) "
                "in its configuration"
            )
        self.harvester = Harvester()
        self.cti_file = configuration["GenTL_producer_cti"]
        try:
            self.harvester.add_file(self.cti_file)
        except OSError as e:  # FileNotFoundError is a subclass
            raise FileNotFoundError(
                f"Could not load GenTL producer file (.cti) at {self.cti_file}"
            ) from e
        self.harvester.update()
        self.cam = self.harvester.create()
        super().__init__(configuration)
        self.cam.remote_device.node_map.CxpLinkConfiguration.value = "CXP12_X4"
        self.cam.remote_device.node_map.PixelFormat.value = "Mono10"
        self.cam.remote_device.node_map.TriggerMode.value = "On"
        self.cam.remote_device.node_map.TriggerSource.value = "Line0"
        self.cam.remote_device.node_map.TriggerSelector.value = "FrameStart"
        self.cam.remote_device.node_map.ExposureTime.value = 20
        self.cam.remote_device.node_map.OffsetX.value = 0
        self.cam.remote_device.node_map.OffsetY.value = 0
        self.cam.remote_device.node_map.Height.value = 800
        self.cam.remote_device.node_map.Width.value = 1200
        self.roi_shape = [
            self.cam.remote_device.node_map.Height.value,
            self.cam.remote_device.node_map.Width.value,
        ]
        self.attribute_map["exposure_time"] = self._set_exposure_time
        self.attribute_map["image_roi"] = self._set_roi
        self.attribute_map["gain"] = self._set_gain
        self.initial_configuration_dict = configuration
        # GenTL needs to be set above.
        self.attribute_map["GenTL_producer_cti"] = self._throw_away_cti
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"GenICamHarvester(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"GenICam Harvester compliant camera instance: GenICamHarvester(configuration: {self.initial_configuration_dict})"

    def _throw_away_cti(self, cti_file: str) -> None:
        """cti must be set at the start. It is therefore ignored here."""

    def _set_exposure_time(self, exposure_time: int) -> None:
        self.cam.remote_device.node_map.ExposureTime.value = exposure_time

    def _set_gain(self, gain: int) -> None:
        self.cam.remote_device.node_map.Gain.value = gain

    def _set_roi(self, roi_shape_and_offset: list[int]) -> None:
        """
        Set the region of interest (ROI) on the camera sensor.

        This method configures the ROI on the camera sensor by setting the height, width,
        and the X and Y offsets. The ROI shape and offsets are specified in a list.

        Args:
            roi_shape_and_offset (List[int]): A list containing four integers:
                - The first two integers specify the height and width of the ROI.
                - The last two integers specify the X and Y offsets of the ROI.

        Raises:
            RuntimeError: If setting any of the ROI parameters on the camera
                fails; the camera's error is kept as ``__cause__``.

        Example:
            roi_shape_and_offset = [height, width, offsetX, offsetY]
            _set_roi(roi_shape_and_offset)
        """
        h, w = roi_shape_and_offset[:2]
        x, y = roi_shape_and_offset[2:]
        roi = f"height {h}, width {w}, offset X {x}, Y {y}"
        node_map = self.cam.remote_device.node_map
        try:
            node_map.Height.value = h
            node_map.Width.value = w
            node_map.OffsetX.value = x
            node_map.OffsetY.value = y
        except Exception as e:
            raise RuntimeError(f"Failed to set GenICam ROI to {roi}") from e
        self.roi_shape = [h, w]
        logger.info(f"Set sensor ROI to {roi}".ljust(65, ".") + "[done]")

    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """
        See :meth:`Sensor.acquire_data`.
        """
        time_1 = time()
        height = self.cam.remote_device.node_map.Height.value
        width = self.cam.remote_device.node_map.Width.value
        self.cam.start()
        data = np.zeros((self.number_measurements,
                         height * width), dtype=np.uint32)
        if synchroniser is not None:
            synchroniser.trigger()
        for i in range(self.number_measurements):
            with self.cam.fetch() as buffer:
                component = buffer.payload.components[0]
                data[i] = component.data
        self.cam.stop()
        time_2 = time()
        logger.info(
            f"Data acquisition took {time_2 - time_1} s".ljust(
                65, ".") + "[done]"
        )
        return data.reshape((self.number_measurements, height, width))

    def open(self) -> None:
        """
        This function simply passes, since all necessary setup happens
        in __init__. This is necessary to configure the camera.
        """

    def close(self) -> None:
        """
        Destroys the camera instance, and resets Harvester.
        Without this, you won't be able to make a new camera instance,
        as the camera will be exclusively owned by this one.
        """
        self.cam.destroy()
        self.harvester.reset()
        logger.info("Closed GenICam camera connection".ljust(
            65, ".") + "[done]")
