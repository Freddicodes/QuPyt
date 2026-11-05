from __future__ import annotations

import ctypes
import gc
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from qupyt.mixins import ConfigurationError

from .base import Sensor

if TYPE_CHECKING:
    from egrabber import Buffer, EGrabber

    from qupyt.hardware.synchroniser import Synchroniser


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
          to select setter methods from an attribute map dictionary to set
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
              Note that we currently do not support the "Bayer" pixel format.
            - **trigger_mode** (string): Sets camera in triggered mode.
              ['On', 'on', 'ON', 'Off', 'off', "OFF"]
            - **trigger_source** (string): Sets input for the camera trigger.
              E.g. 'GPIO0', 'GPIO1'

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.

    Raises (__init__):

        - ConfigurationError
    """

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        try:
            import egrabber
        except ImportError as e:
            raise ImportError(
                "GenICamPhantom needs the Euresys egrabber wheel (see Euresys website)"
            ) from e
        self._eg = egrabber

        self.grabber = self._discover_and_setup()
        super().__init__(configuration)
        self.grabber.remote.set("Height", 200)
        self.grabber.remote.set("Width", 1280)
        self.grabber.remote.set("PixelFormat", "Mono8")
        self.image_dtype: type[np.unsignedinteger[Any]] = np.uint8
        self.grabber.remote.set("TriggerSource", "GPIO0")
        self.grabber.remote.set("TriggerMode", "TriggerModeOn")
        self.grabber.remote.set("ExposureTime", 20)
        self.grabber.stream.set("UnpackingMode", "Lsb")
        self.grabber.remote.set("FlatFieldCorrection", "FlatFieldCorrectionOff")
        self.roi_shape = [
            self.grabber.remote.get("Height") * 4,
            self.grabber.remote.get("Width"),
        ]
        self.attribute_map["exposure_time"] = self._set_exposure_time
        self.attribute_map["image_roi"] = self._set_roi
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
        gentl = self._eg.EGenTL()
        discovery = self._eg.EGrabberDiscovery(gentl)
        discovery.discover()
        if not discovery.cameras:
            raise RuntimeError("No Phantom camera found by EGrabberDiscovery")
        return self._eg.EGrabber(discovery.cameras[0])

    def _set_trigger_source(self, trigger_source: str) -> None:
        """e.g. GPIO0, GPIO1, ..."""
        self.grabber.remote.set("TriggerSource", trigger_source)

    def _set_trigger_mode(self, trigger_mode: str) -> None:
        """
        e.g. On of Off

        Raises:
            - ConfigurationError
        """
        if trigger_mode.lower() == "on":
            self.grabber.remote.set("TriggerMode", "TriggerModeOn")
        elif trigger_mode.lower() == "off":
            self.grabber.remote.set("TriggerMode", "TriggerModeOff")
        else:
            raise ConfigurationError(
                "trigger_mode", trigger_mode, ["on", "off"])

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
        self.grabber.remote.set("PixelFormat", pixel_bits)

    def _set_exposure_time(self, exposure_time: int) -> None:
        self.grabber.remote.set("ExposureTime", exposure_time)

    def _set_roi(self, roi_shape_and_offset: list[int]) -> None:
        """
        Set the region of interest (ROI) of the camera.
        Hardware specific limitations include:
            - Currently it is not possible to change the position of the field of view (FOV).
            Thus, the values for the offsets are completely ignored independent of the chosen value.
            A warning is logged though if they are set to something else than 0.
            - The height can be set from 32 to 800 in steps of 8. The camera also accepts multiples
            of 4 without any warning/error but changes them internally during a measurement.
            - The width can be set from 128 to 1280 in steps of 128.
        """

        roi_shape_h_and_w = roi_shape_and_offset[:2]
        if roi_shape_and_offset[2] or roi_shape_and_offset[3]:
            logger.warning(
                f"Offset X: {roi_shape_and_offset[2]} and Y: {roi_shape_and_offset[3]} "
                "are ignored: the Phantom S710 does not support a ROI offset "
                "(always centered). Set the offset to 0, 0."
            )

        # Checking size compatibility of chosen FOV.
        if roi_shape_h_and_w[0] % 8 != 0:
            raise ValueError("ROI Height for the Phantom S710 has to be a multiple of 8")

        roi_shape_h_and_w[0] = int(np.round(roi_shape_h_and_w[0] / 4))

        if roi_shape_h_and_w[1] % 128 != 0:
            raise ValueError(f"ROI Width for the Phantom S710 has to be a multiple of 128px; {roi_shape_h_and_w[1]}px was specified")
        if roi_shape_h_and_w[1] < 128:
            raise ValueError(f"ROI Width for the Phantom S710 has to be at least 128px; {roi_shape_h_and_w[1]}px was specified")
        if roi_shape_h_and_w[1] > 1280:
            raise ValueError(f"ROI Width for the Phantom S710 has to be at most 1280px; {roi_shape_h_and_w[1]}px was specified")

        if roi_shape_h_and_w[0] > 200:
            raise ValueError(f"ROI Height for the Phantom S710 has to be at most 800px; {roi_shape_h_and_w[0]*4}px  ({roi_shape_h_and_w[0]}px per Sensor) was specified")
        if roi_shape_h_and_w[0] < 8:
            raise ValueError(f"ROI Height for the Phantom S710 has to be at least 32px; {roi_shape_h_and_w[0]*4}px  ({roi_shape_h_and_w[0]}px per Sensor) was specified")

        roi = f"height {roi_shape_h_and_w[0]*4}, width {roi_shape_h_and_w[1]}"
        try:
            self.grabber.remote.set("Height", roi_shape_h_and_w[0])
            self.grabber.remote.set("Width", roi_shape_h_and_w[1])
        except Exception as e:
            raise RuntimeError(f"Failed to set Phantom ROI to {roi}") from e
        self.roi_shape = [roi_shape_h_and_w[0]*4, roi_shape_h_and_w[1]]
        logger.info(f"Set sensor ROI to {roi}".ljust(65, ".") + "[done]")

    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """
        See :meth:`Sensor.acquire_data`.
        """

        height = self.grabber.remote.get("Height")
        width = self.grabber.remote.get("Width")

        self.grabber.realloc_buffers(self.number_measurements)
        self.grabber.start()
        data = np.zeros((self.number_measurements,
                        height * 4, width), dtype=np.uint32)
        timesteps = np.zeros(self.number_measurements, dtype=np.float64)
        if synchroniser is not None:
            synchroniser.trigger()
        for i in range(self.number_measurements):
            with self._eg.Buffer(self.grabber) as buffer:
                buffer_ptr, image_size, part_num, timestep = self._grab_frame_info(
                    buffer)
                raw_frame = self._move_frame_from_pool(
                    buffer_ptr, image_size, part_num)
                data[i] += raw_frame
                timesteps[i] += timestep
        self.grabber.stop()

        self._log_frame_rate(timesteps)

        return data

    @staticmethod
    def _log_frame_rate(timesteps: np.ndarray) -> None:
        """
        Log the frame rate measured from the camera timestamps (in µs).
        Needs at least two frames with distinct timestamps.
        """
        if len(timesteps) < 2:
            logger.info("Frame rate not computed: fewer than two frames acquired")
            return
        span_s = (timesteps[-1] - timesteps[0]) * 1e-6
        if span_s <= 0:
            logger.warning(
                "Frame rate not computed: camera timestamps do not increase (span %s µs)",
                timesteps[-1] - timesteps[0],
            )
            return
        frame_rate = (len(timesteps) - 1) / span_s
        logger.info("Measured frame rate from camera timestamps: %.1f Hz", frame_rate)

    def _grab_frame_info(self, buffer: Buffer) -> tuple[int, int, int, int]:
        buffer_ptr = buffer.get_info(self._eg.BUFFER_INFO_BASE, self._eg.INFO_DATATYPE_PTR)
        image_size = buffer.get_info(
            self._eg.BUFFER_INFO_CUSTOM_PART_SIZE, self._eg.INFO_DATATYPE_SIZET)
        part_num = buffer.get_info(
            self._eg.BUFFER_INFO_CUSTOM_NUM_PARTS, self._eg.INFO_DATATYPE_SIZET)
        time_stamp = buffer.get_info(
            self._eg.BUFFER_INFO_TIMESTAMP, self._eg.INFO_DATATYPE_UINT64)

        return buffer_ptr, image_size, part_num, time_stamp

    def _move_frame_from_pool(self, buffer_ptr: int, image_size: int, part_num: int) -> np.ndarray:
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
        Release the grabber (drop the reference and collect it).
        Without this, you won't be able to make a new camera instance,
        as the camera will be exclusively owned by this one.
        """
        self.grabber = None
        gc.collect()
        logger.info("Closed Phantom camera connection".ljust(
            65, ".") + "[done]")