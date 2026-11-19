import logging
from time import time
from typing import Any, Optional

import libHeLIC as heli
import numpy as np
from qupyt.hardware.base import Synchroniser

from .base import Sensor

logger = logging.getLogger(__name__)


class HeliCam(Sensor, sensor_types=("HeliC3",)):
    """
    `Heliotis <https://www.heliotis.com>`_ builds highly specialised lock-in
    cameras which are very powerfull for specific applications.
    This class focusses on the implementation of their heliCam C3 and its
    raw readout mode.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dicionary to set
          associated values.

          Possible configuration values:
            - **exposure_time** (int/float, µs): This value will get converted
              to heliCam clock cycles. While float values are possible, and may
              provide more fine grained controll, the number of clock cycles will
              be rounded. Therefore, I advise to stick with integer values.
            - **SensNavM2** (list[int]): This is a special parameter used in
              Heliotis cameras. This parameters must be set, as it is vital to
              the operation of the camera. However, we refer to Helioti's
              documentation for the parameters explanation.
            - **number_measurements** (int, even): HeliCam overwrites
              the base :class:`Sensor` class's
              set_number_measurements logic, because every frame produced
              by the heliCam C3 contains two sub frames. We reshape this nested
              frame structure into a flat array of frames in accordance to all
              other camers. However, this implied that internally, the
              configuration for the number of frames works slighly differently.

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.
    """

    def __init__(self, configuration: dict[str, Any]) -> None:
        self.settings = {
            "CamMode": 0,
            "SensNavM2": 6,
            "SensTqp": 24,
            "SensNFrames": 1,
            "BSEnable": 0,
            "DdsGain": 2,
            "TrigFreeExtN": 0,
            "ExtTqp": 1,
            "EnTrigOnPos": 0,
            "EnSynFOut": 0,
            "AcqStop": 0,
        }
        super().__init__(configuration)
        self.attribute_map["SensNavM2"] = self._set_SensNavM2
        self.attribute_map["exposure_time"] = self._set_SensTqp
        self.attribute_map["number_measurements"] = self._set_number_measurements
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)
        self.he_sys = heli.LibHeLIC()
        self.roi_shape = [300, 300]

    def __repr__(self) -> str:
        return f"HeliCam(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"HeliCam C3 camera instance: HeliCam(configuration: {self.initial_configuration_dict})"

    def _set_SensTqp(self, exposure_time: int) -> None:
        # Clockcyle of camera is 35 MHz -> 35 cycles per mus
        SensTqp = exposure_time * 35 - 11
        self.settings["SensTqp"] = int(SensTqp)

    def _set_SensNavM2(self, SensNavM2: int) -> None:
        self.settings["SensNavM2"] = int(SensNavM2)

    def _set_number_measurements(self, number_measurements: int) -> None:
        self.settings["SensNFrames"] = int(number_measurements / 2)
        self.number_measurements = int(number_measurements)

    def open(self) -> None:
        """
        Opens the camera and loads the firmware.
        Flushes the camera buffer, and sets the cameras attribute map.
        """
        self.he_sys.Open(0, sys="c3cam_sl70")
        res = 1
        while res > 0:
            res = self.he_sys.Acquire()
        for key, value in self.settings.items():
            setattr(self.he_sys.map, key, value)
        self.he_sys.AllocCamData(1, heli.LibHeLIC.CamDataFmt["DF_I16Q16"], 0, 0, 0)
        self.he_sys.SetTimeout(10000)
        logger.info("Heli C3 opened".ljust(65, ".") + "[done]")

    def acquire_data(self, synchroniser: Optional[Synchroniser] = None) -> np.ndarray:
        """
        Reads frames from the camera and reshuffels from the
        heliCam specific format to the structure
        we expect from other sensors.

        See :meth:`Sensor.acquire_data`.
        """
        time_1 = time()
        if synchroniser is not None:
            synchroniser.trigger()
        res = self.he_sys.Acquire()
        if res < 0:
            logger.warning(
                "Your camera appears to have timed out".ljust(65, ",") + "[done]"
            )
        _ = self.he_sys.ProcessCamData(1, 0, 0)
        meta = self.he_sys.CamDataMeta()
        img = self.he_sys.GetCamData(1, 0, heli.ct.byref(meta))
        data = img.contents.data
        data = heli.LibHeLIC.Ptr2Arr(
            data, (self.settings["SensNFrames"], 300, 300, 2), heli.ct.c_ushort
        )
        raw_frames = np.asarray((data), dtype="int32")
        return_frames = np.zeros((2 * self.settings["SensNFrames"], 300, 300))
        return_frames[::2] = raw_frames[:, :, :, 0]
        return_frames[1::2] = raw_frames[:, :, :, 1]
        time_2 = time()
        logger.info(
            f"HeliCam C3 data acquisition took {time_2 - time_1} s".ljust(65, ".")
            + "[done]"
        )
        return return_frames

    def close(self) -> None:
        """Closes the the camera.
        A new camera instance may now be created."""
        self.he_sys.Close()
        logger.info("HeliCam C3 closed".ljust(65, ".") + "[done]")
