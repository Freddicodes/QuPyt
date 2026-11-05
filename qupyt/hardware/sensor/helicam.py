from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from time import monotonic, time
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from qupyt.mixins import ConfigurationError
from qupyt.utils.decorators import log_sensor_acquisition
from qupyt.utils.errors import cleanup_after_error, close_on_initialization_error

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser import Synchroniser

logger = logging.getLogger(__name__)


def _helic_wrapper_dir() -> Path:
    if override := os.environ.get("QUPYT_HELIC_PATH"):
        return Path(override)
    if sys.platform == "win32":
        return (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Heliotis/heliCam/Python/wrapper"
        )
    return Path("/usr/share/libhelic/python/wrapper")


def _import_libhelic() -> ModuleType:
    wrapper = str(_helic_wrapper_dir())
    if wrapper not in sys.path:
        sys.path.insert(0, wrapper)
    try:
        import libHeLIC  # pylint: disable=import-outside-toplevel
    except ImportError as e:
        raise ImportError(
            f"HeliCam needs libHeLIC from the Heliotis heliCam SDK "
            f"(looked in {wrapper}; set QUPYT_HELIC_PATH to override)."
        ) from e
    # libHeLIC is untyped (ignored import), so mypy sees Any.
    return cast(ModuleType, libHeLIC)


class HeliCam(Sensor, sensor_types=("HeliC3",)):
    """`Heliotis <https://www.heliotis.com>`_ builds highly specialized lock-in cameras which are very powerful for specific applications.

    This class focuses on the implementation of their heliCam C3 and its raw
    readout mode.

    Parameters
    ----------
    configuration : dict
        Configuration dictionary. Keys will be used
        to select setter methods from an attribute map dictionary to set
        associated values.

        Possible configuration values:
          - **exposure_time** (int/float, µs): This value will get converted
            to heliCam clock cycles. While float values are possible, and may
            provide more fine-grained control, the number of clock cycles will
            be rounded. Therefore, I advise to stick with integer values.
          - **SensNavM2** (int, optional, default ``6``): Heliotis-specific
            sensor setting that is vital to the operation of the camera;
            see the Heliotis documentation for its meaning.
          - **number_measurements** (int, even): HeliCam overwrites
            the base :class:`Sensor` class's
            set_number_measurements logic, because every frame produced
            by the heliCam C3 contains two sub frames. We reshape this nested
            frame structure into a flat array of frames in accordance to all
            other cameras. However, this implied that internally, the
            configuration for the number of frames works slightly differently.

        Note that these configuration attributes extend those from the
        :class:`Sensor` base class.
    """

    @close_on_initialization_error
    def __init__(self, configuration: dict[str, Any] | None) -> None:

        self._is_open = False
        self._heli = _import_libhelic()

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
        self.he_sys = self._heli.LibHeLIC()
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
        if int(number_measurements) % 2:
            raise ConfigurationError(
                "number_measurements", number_measurements, "an even integer"
            )
        self.settings["SensNFrames"] = int(number_measurements) // 2
        self.number_measurements = int(number_measurements)

    def open(self) -> None:
        """Open the camera (system ``c3cam_sl70``), flush pending frames, write the stored settings (exposure, ``SensNavM2``, ...) to the camera's register map, allocate the data buffer and set a 10 s timeout.

        Raises
        ------
        TimeoutError
            If flushing pending frames takes longer than 10 s.
        """
        if self._is_open:
            return
        try:
            self.he_sys.Open(0, sys="c3cam_sl70")
            self._is_open = True
            # Set the timeout first so the flush below cannot block forever.
            self.he_sys.SetTimeout(10000)
            deadline = monotonic() + 10
            res = 1
            while res > 0:
                if monotonic() >= deadline:
                    raise TimeoutError(
                        "HeliCam buffer flush did not finish within 10 seconds"
                    )
                res = self.he_sys.Acquire()
            for key, value in self.settings.items():
                setattr(self.he_sys.map, key, value)
            self.he_sys.AllocCamData(
                1, self._heli.LibHeLIC.CamDataFmt["DF_I16Q16"], 0, 0, 0
            )
        except BaseException:
            logger.exception("HeliCam setup failed")
            cleanup_after_error(self.close, "HeliCam setup cleanup")
            raise
        logger.info("Heli C3 opened".ljust(65, ".") + "[done]")

    @log_sensor_acquisition
    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """Read frames and reorder them from the heliCam format to the common sensor layout.

        See :meth:`Sensor.acquire_data`.
        """
        time_1 = time()
        if synchroniser is not None:
            synchroniser.trigger()
        res = self.he_sys.Acquire()
        if res < 0:
            raise TimeoutError(
                f"HeliCam acquisition failed or timed out (status={res})"
            )
        _ = self.he_sys.ProcessCamData(1, 0, 0)
        meta = self.he_sys.CamDataMeta()
        img = self.he_sys.GetCamData(1, 0, self._heli.ct.byref(meta))
        data = img.contents.data
        h, w = self.roi_shape
        data = self._heli.LibHeLIC.Ptr2Arr(
            data, (self.settings["SensNFrames"], h, w, 2), self._heli.ct.c_ushort
        )
        raw_frames = np.asarray(data, dtype="int32")
        return_frames = np.zeros((2 * self.settings["SensNFrames"], h, w))
        return_frames[::2] = raw_frames[:, :, :, 0]
        return_frames[1::2] = raw_frames[:, :, :, 1]
        time_2 = time()
        logger.info(
            f"HeliCam C3 data acquisition took {time_2 - time_1} s".ljust(65, ".")
            + "[done]"
        )
        return return_frames

    def close(self) -> None:
        """Close the camera.

        A new camera instance may now be created. Safe to call twice.
        """
        if not getattr(self, "_is_open", False):
            return
        self.he_sys.Close()
        self._is_open = False
        logger.info("HeliCam C3 closed".ljust(65, ".") + "[done]")
