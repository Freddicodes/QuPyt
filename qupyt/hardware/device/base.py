from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from qupyt.mixins import ConfigurationMixin, UnknownDeviceError, UpdateConfigurationType


class Device(ABC, ConfigurationMixin):
    """
    Abstract Base Class for all devices: every part of the setup that is
    neither the sensor nor the synchroniser, e.g. signal sources or power
    supplies.

    Devices are listed under ``static_devices`` or ``dynamic_devices`` in the
    measurement YAML file. The :class:`~qupyt.hardware.device_handler.DeviceHandler`
    treats all of them the same and only relies on three methods:

    - :meth:`set_values` applies the current configuration to the hardware,
    - :meth:`update_configuration` replaces the configuration (dynamic devices
      get a new one for every measurement step),
    - :meth:`close` releases the connection.

    Which configuration keys a device accepts is defined by its
    ``attribute_map``: each key maps to a setter method. Device families
    (e.g. :class:`SignalSource`) add the keys they share, concrete devices
    add their own.

    Subclasses register themselves under one or more names via the
    ``device_types`` class keyword. The name is the ``device_type`` given in
    the YAML file::

        class MockSignalSource(SignalSource, device_types=("Mock",)):
            ...

        device = Device.create("Mock", "some-address", {"frequency": 2.87e9})

    Note:
        Configure the device with the ``configuration`` constructor argument;
        its attributes are not meant to be set directly.

    Arguments:
        - **configuration** (dict | None): Configuration dictionary. Keys
          select setter methods from the ``attribute_map``. It is only stored
          here; it is applied by :meth:`set_values`.
    """

    _registry: ClassVar[dict[str, type[Device]]] = {}
    device_types: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(
        cls, device_types: tuple[str, ...] = (), **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.device_types = device_types

        if len(set(device_types)) != len(device_types):
            raise TypeError(
                f"Tried to register duplicate device types by '{cls.__name__}'"
            )

        for name in device_types:
            if name in Device._registry:
                raise TypeError(
                    f"Device type {name!r} already registered by "
                    f"{Device._registry[name].__name__}"
                )
        for name in device_types:
            Device._registry[name] = cls

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        self.configuration = configuration
        self.attribute_map: UpdateConfigurationType = {}

    @classmethod
    def create(
        cls,
        device_type: str,
        address: str,
        configuration: dict[str, Any] | None,
    ) -> Device:
        """
        Create a registered device. The configuration is stored, not yet
        applied; call :meth:`set_values` for that.

        :param device_type: Registered name, e.g. ``'Mock'`` or ``'SMA'``.
        :type device_type: str
        :param address: Address used to connect to the device, e.g.
            ``'TCPIP::<ip>::INSTR'`` or a serial port.
        :type address: str
        :param configuration: Configuration parameters for the device.
            Provided keys need to match the device's ``attribute_map``.
        :type configuration: dict
        :return: Instance of the requested device.
        :rtype: Device
        :raises UnknownDeviceError: if no device is registered under that name.
        """
        try:
            device_cls = cls._registry[device_type]
        except KeyError:
            raise UnknownDeviceError(device_type, cls.available()) from None
        return device_cls._from_config(device_type, address, configuration)

    @classmethod
    def available(cls) -> list[str]:
        """
        :return: All registered device names, sorted.
        :rtype: list[str]
        """
        return sorted(cls._registry)

    @classmethod
    def _from_config(
        cls,
        device_type: str,
        address: str,
        configuration: dict[str, Any] | None,
    ) -> Device:
        """
        Call the constructor of ``cls`` (the registered class, not
        :class:`Device`). Most devices take ``(address, configuration)``;
        classes with a different constructor override this method, e.g. the
        VISA signal sources also need the ``device_type``.

        :param device_type: Registered name the device was requested with.
        :type device_type: str
        :param address: Address used to connect to the device.
        :type address: str
        :param configuration: Configuration parameters for the device.
        :type configuration: dict
        :return: Instance of ``cls``.
        :rtype: Device
        """
        # mypy only knows Device.__init__(configuration); the concrete
        # classes this is called on take (address, configuration).
        return cls(address, configuration)  # type: ignore[call-arg, arg-type]

    def set_values(self) -> None:
        """
        Apply the current configuration to the device: every key calls its
        setter from the ``attribute_map``. Keys that are not in the
        configuration are left unchanged.

        :raises KeyError: if a key is not in the ``attribute_map``.
        """
        if self.configuration is not None:
            self._update_from_configuration(self.configuration)

    def update_configuration(self, config: dict[str, Any]) -> None:
        """
        Replace the stored configuration (not merged). Nothing is sent to the
        device until :meth:`set_values` is called.

        :param config: The new configuration.
        :type config: dict
        """
        self.configuration = config

    @abstractmethod
    def close(self) -> None:
        """
        Release the connection to the device.
        """
