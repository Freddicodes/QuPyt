import logging
from time import time
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from pypylon import pylon
from qupyt.exceptions import ConfigurationError

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser.base import Synchroniser

logger = logging.getLogger(__name__)


class BaslerCam(Sensor, sensor_types=("Basler1920",)):
    """
    Sensor class implementation for all Basler Mono cameras.
    While (most or all) Basler cameras are fully GenICam compliant
    and will work with the :class:`GenICam_Harvester` class, some
    users might prefer to use `pypylon <https://github.com/basler/pypylon>`_,
    which is provieded by Basler direclty. Note that Baser strongly
    recommends you install
    `pylon <https://www.baslerweb.com/en/products/basler-pylon-camera-software-suite/>`_
    from their website.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dicionary to set
          associated values.

          Possible configuration values:
            - **exposure_time** (int, µs)
            - **image_roi** (list[int]): Region Of Interest of the sensor in
              the following format: [height, width, x_offset, y_offset].
              The roi_shape attribute of the :class:`Sensor` base class will
              be derived from this.
            - **binning_horizontal** (string): Options typically are 1, 2, 3 or 4.
              However, this might depend on your specific camera model.
            - **binning_vertical** (string): Options are typically 1, 2, 3 or 4.
              However, this might depend on your specific camera model.
            - **binning_mode_horizontal** (string): Options are `'sum'` or `'average'`
            - **binning_mode_vertical** (string): Options are `'sum'` or `'average'`

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.
    """

    def __init__(self, configuration: dict[str, Any]) -> None:
        self.cam = pylon.InstantCamera(
            pylon.TlFactory.GetInstance().CreateFirstDevice()
        )
        self.cam.Open()
        self._configure_defaults()
        self._configure_const()
        super().__init__(configuration)
        self.attribute_map["exposure_time"] = self._set_exposure_time
        self.attribute_map["binning_horizontal"] = self._set_binning_horizontal
        self.attribute_map["trigger_line"] = self._set_trigger_line
        self.attribute_map["trigger_mode"] = self._set_trigger_mode
        self.attribute_map["binning_vertical"] = self._set_binning_vertical
        self.attribute_map["binning_mode_horizontal"] = (
            self._set_mode_binning_horizontal
        )
        self.attribute_map["binning_mode_vertical"] = self._set_mode_binning_vertical
        self.attribute_map["image_roi"] = self._set_roi
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"BaslerCam(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"Basler pylon camera instance: BaslerCam(configuration: {self.initial_configuration_dict})"

    def _configure_defaults(self) -> None:
        self.cam.BinningHorizontal.SetValue(1)
        self.cam.BinningVertical.SetValue(1)
        self.cam.BinningHorizontalMode.SetValue("Sum")
        self.cam.BinningVerticalMode.SetValue("Sum")
        self.cam.OffsetX.SetValue(0)
        self.cam.OffsetY.SetValue(0)
        self.cam.Height.SetValue(540)
        self.cam.Width.SetValue(220)
        self.cam.ExposureTime.SetValue(700)

    def _configure_const(self) -> None:
        self.cam.TriggerActivation.SetValue("RisingEdge")
        self.cam.Gain.SetValue(0)
        self.cam.TriggerSelector.SetValue("FrameStart")
        self.cam.TriggerMode.SetValue("On")
        self.cam.TriggerSource.SetValue("Line3")
        self.cam.LineSelector.SetValue("Line3")
        self.cam.LineInverter.SetValue(False)
        self.cam.LineMode.SetValue("Input")
        self.cam.PixelFormat.SetValue("Mono12")
        self.cam.ExposureMode.SetValue("Timed")

    def acquire_data(self, synchroniser: Optional[Synchroniser] = None) -> np.ndarray:
        """
        See :meth:`Sensor.acquire_data`.
        """
        time_1 = time()
        arr = np.zeros((self.number_measurements, *self.roi_shape))
        self.cam.StartGrabbingMax(self.number_measurements)
        if synchroniser is not None:
            synchroniser.trigger()
        for i in range(self.number_measurements):
            grab_result = self.cam.RetrieveResult(
                int(5000), pylon.TimeoutHandling_ThrowException
            )
            frame = np.asarray(grab_result.Array)
            arr[i] = frame
            grab_result.Release()
        time_2 = time()
        logger.info(
            f"Basler data acquisition took {time_2 - time_1} s".ljust(65, ".")
            + "[done]"
        )
        return arr

    def _set_trigger_line(self, trigger_line):
        self.cam.TriggerSelector.SetValue("FrameStart")
        self.cam.TriggerMode.SetValue("On")
        self.cam.TriggerSource.SetValue(trigger_line)
        self.cam.LineSelector.SetValue(trigger_line)
        self.cam.LineInverter.SetValue(False)
        self.cam.LineMode.SetValue("Input")

    def _set_trigger_mode(self, trigger_mode: str) -> None:
        if trigger_mode.lower() == "on":
            self.cam.TriggerMode.SetValue("On")
        elif trigger_mode.lower() == "off":
            self.cam.TriggerMode.SetValue("Off")
        else:
            raise ConfigurationError("trigger_mode", trigger_mode, ["on", "off"])

    def _set_exposure_time(self, exposure_time: int) -> None:
        self.cam.ExposureTime.SetValue(exposure_time)

    def _set_binning_horizontal(self, binning_horizontal: int) -> None:
        self.cam.BinningHorizontal.SetValue(binning_horizontal)

    def _set_binning_vertical(self, binning_vertical: int) -> None:
        self.cam.BinningVertical.SetValue(binning_vertical)

    def _set_mode_binning_horizontal(self, mode_binning_horizontal: str) -> None:
        self.cam.BinningHorizontalMode.SetValue(mode_binning_horizontal)

    def _set_mode_binning_vertical(self, mode_binning_vertical: str) -> None:
        self.cam.BinningVerticalMode.SetValue(mode_binning_vertical)

    def _set_roi(self, roi_shape_and_offset: list[int]) -> None:
        """
        Set the region of interest (ROI) on the camera sensor.

        This method configures the ROI on the camera sensor by setting the height, width,
        and the X and Y offsets. The ROI shape and offsets are specified in a list.

        Args:
            roi_shape_and_offset (list[int]): A list containing four integers:
                - The first two integers specify the height and width of the ROI.
                - The last two integers specify the X and Y offsets of the ROI.

        Raises:
            Exception: If setting any of the ROI parameters on the camera fails, an exception is logged and raised.

        Example:
            roi_shape_and_offset = [height, width, offsetX, offsetY]
            _set_roi(roi_shape_and_offset)

        The method logs the outcome of setting the ROI:
            - On success: Logs the height, width, and offsets along with a "[done]" status.
            - On failure: Logs the height, width, and offsets along with a "[failed]" status and raises the exception.
        """
        roi_shape_h_and_w = roi_shape_and_offset[:2]
        roi_offset_x_and_y = roi_shape_and_offset[2:]
        try:
            self._set_roi_shape(roi_shape_h_and_w)
            self._set_roi_offset_x_and_y(roi_offset_x_and_y)
            self.roi_shape = roi_shape_h_and_w
            logger.info(
                "Set Sensor roi to height: {} and width: {}\n\
                          with offset X: {} and Y: {}".format(
                    roi_shape_h_and_w[0],
                    roi_shape_h_and_w[1],
                    roi_offset_x_and_y[0],
                    roi_offset_x_and_y[1],
                ).ljust(65, ".")
                + "[done]"
            )
        except Exception as exc:
            logger.exception(
                "Failed to set roi of height: {} and width {}\n\
                               with offset X: {} and Y: {}".format(
                    roi_shape_h_and_w[0],
                    roi_shape_h_and_w[1],
                    roi_offset_x_and_y[0],
                    roi_offset_x_and_y[1],
                ).ljust(65, ".")
                + "[failed]"
            )
            raise exc

    def _set_roi_shape(self, roi_shape_h_and_w: list[int]) -> None:
        self.cam.Height.SetValue(roi_shape_h_and_w[0])
        self.cam.Width.SetValue(roi_shape_h_and_w[1])

    def _set_roi_offset_x_and_y(self, roi_offset_x_and_y: list[int]) -> None:
        self.cam.OffsetX.SetValue(roi_offset_x_and_y[0])
        self.cam.OffsetY.SetValue(roi_offset_x_and_y[1])

    def open(self) -> None:
        """Opens camera."""
        logger.info("Opening Balser".ljust(65, ".") + "[done]")

    def close(self) -> None:
        """Closes the the camera.
        A new camera instance may now be created."""
        self.cam.Close()
        logger.info("Closed Basler camera connection".ljust(65, ".") + "[done]")
