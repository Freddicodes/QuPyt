import ctypes
import gc
import logging
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
from qupyt.exceptions import ConfigurationError
from qupyt.utils.imports import install_hint

from .base import Sensor

if TYPE_CHECKING:
    from egrabber import Buffer, EGrabber
    from qupyt.hardware.synchroniser.base import Synchroniser

logger = logging.getLogger(__name__)


class GenICamPhantom(Sensor, sensor_types=("PhantomS710",)):
    """
    Sensor class implementation for the PhantomS710 GenICam compliant camera.
    It builds on the EGrabber library provided by Euresys.
    Therefore, it will only work with the Framegrabber / Camera combination.

    Note:
        You will need to install the egrabber software and the egrabber python
        wheel provided on the Euresys website for this class to work.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dicionary to set
          associated values.

          Possible configuration values:
            - **exposure_time** (int, µs)
            - **image_roi** (list[int]):
              Region Of Interest of the sensor in
              the following format: [height, width, x_offset, y_offset]. The roi_shape attribute of the :class:`Sensor` base class will
              be derived from this. The Phantom S710 only supports size adjustment with height (32 to 800 in steps of 8)
              and width (128 to 1280 in steps of 128), but does not support a ROI that is off-center
              (thus ignoring these input values). The offset input is kept nevertheless to keep it structurally alike other camera inputs.
            - **pixel_bits** (string): Sets number of bits per pixel.
              E.g. 'Mono8', 'Mono12' or 'Mono16'.
              Note that we currently do not support the "bayer" pixel format.
            - **trigger_mode** (string): Sets camera in triggered mode.
              ['On', 'on', 'ON', 'Off', 'off', "OFF"]
            - **trigger_source** (string): Sets input for the camera trigger.
              E.g. 'GPIO0', 'GPIO1'

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.

    Raises (__init__):

        - ConfigurationError
    """

    def __init__(self, configuration: dict[str, Any]) -> None:
        with install_hint(vendor="Install the eGrabber SDK and its Python package from Euresys."):
            import egrabber

        self._egrabber = egrabber
        self.cam, self.cam_instance = self._discover_and_setup()
        super().__init__(configuration)
        # self.cam.remote_device.node_map.OffsetX.value = 0
        # self.cam.remote_device.node_map.OffsetY.value = 0
        self.cam.remote.set("Height", 200)
        self.cam.remote.set("Width", 1280)
        self.cam.remote.set("PixelFormat", "Mono8")
        self.image_dtype = np.uint8
        self.cam.remote.set("TriggerSource", "GPIO0")
        self.cam.remote.set("TriggerMode", "TriggerModeOn")
        self.cam.remote.set("ExposureTime", 20)
        self.cam.stream.set("UnpackingMode", "Lsb")
        self.cam.stream.set("UnpackingMode", "Lsb")
        self.cam.remote.set("FlatFieldCorrection", "FlatFieldCorrectionOff")
        self.roi_shape = [
            self.cam.remote.get("Height") * 4,
            self.cam.remote.get("Width"),
        ]
        self.attribute_map["exposure_time"] = self._set_exposure_time
        self.attribute_map["image_roi"] = self._set_roi
        # self.attribute_map['gain'] = self._set_gain
        self.attribute_map["pixel_bits"] = self._set_pixel_bits
        self.attribute_map["trigger_mode"] = self._set_trigger_mode
        self.attribute_map["trigger_source"] = self._set_trigger_source
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"GenICamPhantom(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"Phantom S710 camera instance: GenICamPhantom(configuration: {self.initial_configuration_dict})"

    def _discover_and_setup(self) -> EGrabber:
        gentl = self._egrabber.EGenTL()
        discovery = self._egrabber.EGrabberDiscovery(gentl)
        discovery.discover()
        cam = discovery.cameras[0]
        self.grabber = self._egrabber.EGrabber(cam)
        return self.grabber, cam

    def _set_trigger_source(self, trigger_source: str) -> None:
        """e.g. GPIO0, GPIO1, ..."""
        self.cam.remote.set("TriggerSource", trigger_source)

    def _set_trigger_mode(self, trigger_mode: str) -> None:
        """
        e.g. On of Off

        Raises:
            - ConfigurationError
        """
        if trigger_mode.lower() == "on":
            self.cam.remote.set("TriggerMode", "TriggerModeOn")
        elif trigger_mode.lower() == "off":
            self.cam.remote.set("TriggerMode", "TriggerModeOff")
        else:
            raise ConfigurationError("trigger_mode", trigger_mode, ["on", "off"])

    def _set_pixel_bits(self, pixel_bits: str) -> None:
        """
        e.g. Mono8, Mono12 or Mono16

        Raises:
            - ConfigurationError
        """
        if pixel_bits.lower() == "mono8":
            self.image_dtype = np.uint8
        elif pixel_bits.lower() in ["mono12", "mono16"]:
            self.image_dtype = np.uint16
        else:
            #  The 'bayer' pixel format is not supported
            raise ConfigurationError(
                "pixel_bits", pixel_bits, ["mono8", "mono12", "mono16"]
            )
        self.cam.remote.set("PixelFormat", pixel_bits)

    def _set_exposure_time(self, exposure_time: int) -> None:
        self.cam.remote.set("ExposureTime", exposure_time)

    # def _set_gain(self, gain: int) -> None:
    #     self.cam.remote_device.node_map.Gain.value = gain

    def _set_roi(self, roi_shape_and_offset: list[int]) -> None:
        """
        This functions sets the range of interest (ROI) of the camera.
        Hardware specific limitations include:
            - Currently it is not possible to change the position of the field of view (FOV).
            Thus, the values for the offsets are completely ignored independent of the chosen value.
            A warning is logged though if they are set to something else than 0.
            - The height can be set from 32 to 800 in steps of 8. The camera will accept values with an integer
            of 4 without any warning/error but will internally change them upon a measurement.
            - The width can be set from 128 to 1280 in steps of 128.
        """

        roi_shape_h_and_w = roi_shape_and_offset[:2]
        if roi_shape_and_offset[2] or roi_shape_and_offset[3]:
            logger.warning(
                f"Offset X:{roi_shape_and_offset[2]} and Y:{roi_shape_and_offset[3]} are ignored, as the Phantom S710\n"
                + f"does not support ROI offset and is always centered. Consider resizing the ROI to 0,0.".ljust(
                    65, "."
                )
                + "[warning]"
            )

        # Checking size compatibility of chosen FOV.
        if roi_shape_h_and_w[0] % 8 != 0:
            raise ValueError(
                "ROI Height for the Phantom S710 has to be a multiple of 8"
            )

        roi_shape_h_and_w[0] = int(np.round(roi_shape_h_and_w[0] / 4))

        if roi_shape_h_and_w[1] % 128 != 0:
            raise ValueError(
                f"ROI Width for the Phantom S710 has to be a multiple of 128px; {roi_shape_h_and_w[1]}px was specified"
            )
        if roi_shape_h_and_w[1] < 128:
            raise ValueError(
                f"ROI Width for the Phantom S710 has to be at least 128px; {roi_shape_h_and_w[1]}px was specified"
            )
        if roi_shape_h_and_w[1] > 1280:
            raise ValueError(
                f"ROI Width for the Phantom S710 has to be at most 1280px; {roi_shape_h_and_w[1]}px was specified"
            )

        if roi_shape_h_and_w[0] > 200:
            raise ValueError(
                f"ROI Height for the Phantom S710 has to be at most 800px; {roi_shape_h_and_w[0] * 4}px  ({roi_shape_h_and_w[0]}px per Sensor) was specified"
            )
        if roi_shape_h_and_w[0] < 8:
            raise ValueError(
                f"ROI Height for the Phantom S710 has to be at least 32px; {roi_shape_h_and_w[0] * 4}px  ({roi_shape_h_and_w[0]}px per Sensor) was specified"
            )

        try:
            self.cam.remote.set("Height", roi_shape_h_and_w[0])
            self.cam.remote.set("Width", roi_shape_h_and_w[1])
            self.roi_shape = [roi_shape_h_and_w[0] * 4, roi_shape_h_and_w[1]]
            logger.info(
                f"Set Sensor roi to height: {roi_shape_h_and_w[0] * 4} and width: {roi_shape_h_and_w[1]}\n".ljust(
                    65, "."
                )
                + "[done]"
            )
        except Exception as exc:
            logger.exception(
                f"Failed to set roi of height: {roi_shape_h_and_w[0] * 4} and width: {roi_shape_h_and_w[1]}\n".ljust(
                    65, "."
                )
                + "[failed]"
            )
            raise exc

    def acquire_data(self, synchroniser: Optional[Synchroniser] = None) -> np.ndarray:
        """
        See :meth:`Sensor.acquire_data`.
        """

        height = self.cam.remote.get("Height")
        width = self.cam.remote.get("Width")

        self.cam.realloc_buffers(self.number_measurements)
        self.cam.start()
        data = np.zeros((self.number_measurements, height * 4, width), dtype=np.uint32)
        timesteps = np.zeros(self.number_measurements, dtype=np.float64)
        if synchroniser is not None:
            synchroniser.trigger()
        for i in range(self.number_measurements):
            with self._egrabber.Buffer(self.cam) as buffer:
                buffer_ptr, image_size, part_num, timestep = self._grab_frame_info(
                    buffer
                )
                raw_frame = self._move_frame_from_pool(buffer_ptr, image_size, part_num)
                data[i] += raw_frame
                timesteps[i] += timestep
        self.cam.stop()

        logger.info(
            f"The real framerate calculated by the timing of the camera is {1 / ((timesteps[self.number_measurements - 1] - timesteps[0]) * 1e-6 / (self.number_measurements - 1))} Hz".ljust(
                65, "."
            )
            + "[info]"
        )

        return data

    def _grab_frame_info(self, buffer: Buffer):
        buffer_ptr = buffer.get_info(self._egrabber.BUFFER_INFO_BASE, self._egrabber.INFO_DATATYPE_PTR)
        image_size = buffer.get_info(self._egrabber.BUFFER_INFO_CUSTOM_PART_SIZE, self._egrabber.INFO_DATATYPE_SIZET)
        part_num = buffer.get_info(self._egrabber.BUFFER_INFO_CUSTOM_NUM_PARTS, self._egrabber.INFO_DATATYPE_SIZET)
        time_stamp = buffer.get_info(self._egrabber.BUFFER_INFO_TIMESTAMP, self._egrabber.INFO_DATATYPE_UINT64)

        return buffer_ptr, image_size, part_num, time_stamp

    def _move_frame_from_pool(self, buffer_ptr, image_size, part_num) -> np.array:
        frame = np.empty(
            (part_num * self.roi_shape[0], self.roi_shape[1]), dtype=self.image_dtype
        )
        frame_ptr = frame.ctypes.data_as(ctypes.c_void_p)
        ctypes.memmove(frame_ptr, buffer_ptr, image_size * part_num)
        return frame

    def open(self) -> None:
        """
        This function simply passes, since all necessary setup happens
        in __init__. This is necessary to configure the camera.
        """

    def close(self) -> None:
        """
        Destroys the camera instance.
        Without this, you won't be able to make a new camera instance,
        as the camera will be exclusively owned by this one.
        """
        self.cam = None
        self.cam_instance = None
        self.grabber = None
        gc.collect()
        logger.info("Closed GenICam camera connection".ljust(65, ".") + "[done]")
