from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

import numpy as np
from numpy.typing import DTypeLike

from qupyt.mixins import ConfigurationMixin, UnknownSensorError

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser import Synchroniser

class Sensor(ABC, ConfigurationMixin):
    """
    Abstract Base Class for all sensors. All sensors implemented in QuPyt
    should inherit from this class. This helps ensure compliance with the
    sensor API.

    Note:
        The attributes listed below are never explicitly set by the user.
        Please use the ``configuration`` constructor argument to configure the sensor.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dictionary to set
          associated values.

          Possible configuration values:
            - **number_measurements** (int, even): Set the number of
              images or measurements the sensor will acquire in one go.

          Concrete sensor classes may have additional configuration values.

    Attributes:
        - **roi_shape** (list[int]): Specifies the shape (Region Of Interest)
          that's read out from the sensor. This attribute is present to
          communicate the sensor dimensions to other parts of the code.
          It should not be actively set by the user.
          Check the configuration options for the individual sensors.
        - **target_data_type** (numpy dtype or name, e.g. ``"uint16"``): Datatype returned by the sensor.
          It should match or at least be compatible with the datatype of the
          data container. Not settable for all sensors.
        - **number_measurements** (int): Set by the number_measurements
          attribute in the configuration dict.
    """
    _registry: ClassVar[dict[str, type[Sensor]]] = {}
    sensor_types: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, sensor_types: tuple[str, ...] = (), **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.sensor_types = sensor_types

        if len(set(sensor_types)) != len(sensor_types):
            raise TypeError(
                f"Tried to register duplicate sensor types by '{cls.__name__}'"
            )

        for name in sensor_types:
            if name in Sensor._registry:
                raise TypeError(
                    f"Sensor type {name!r} already registered by "
                    f"{Sensor._registry[name].__name__}"
                )
        for name in sensor_types:
            Sensor._registry[name] = cls

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        self.roi_shape: list[int]
        self.number_measurements: int = 2
        self.target_data_type: DTypeLike
        self.attribute_map = {
            "number_measurements": lambda x: setattr(self, "number_measurements", x),
            "target_data_type": lambda x: setattr(self, "target_data_type", x),
        }

        self.configuration = configuration

    @classmethod
    def create(cls, sensor_type: str, configuration: dict[str, Any] | None) -> Sensor:
        """
        Create and configure a registered sensor.

        :param sensor_type: Registered name, e.g. ``'MockCam'``.
        :type sensor_type: str
        :param configuration: Configuration parameters for the sensor.
        :type configuration: dict | None
        :return: Instance of the requested sensor.
        :rtype: Sensor
        :raises UnknownSensorError: if no sensor is registered under that name.
        """
        try:
            sensor_cls = cls._registry[sensor_type]
        except KeyError:
            raise UnknownSensorError(sensor_type, cls.available()) from None
        return sensor_cls(configuration)
    
    @classmethod
    def available(cls) -> list[str]:
        """
        :return: All registered sensor names, sorted.
        :rtype: list[str]
        """
        return sorted(cls._registry)

    @abstractmethod
    def open(self) -> None:
        """
        Implements the discovery and establishes the connection
        to a sensor.
        Called once per measurement, before the first acquire_data
        """

    @abstractmethod
    def acquire_data(self, synchroniser: Synchroniser | None) -> np.ndarray:
        """
        Returns measurements in an array of shape
        [self.number_measurements, \\*self.roi_shape].

        :param synchroniser: Synchronisers instance.
         Sensor and Synchroniser are in most cases the devices that
         need the most amount of configuration. To avoid problems
         with one device being ready first, the synchronisers
         is prepared first. Next the sensor is made ready. Once
         ready, the sensor sends a trigger signal to the
         synchronisers to kick off the measurement.
        :type synchroniser: Optional[Synchroniser]
        """

    @abstractmethod
    def close(self) -> None:
        """
        Closes and if necessary destroys the sensor instance.
        Called once at the end, also after a failure. Must be safe to call if open() failed or never ran.
        """

