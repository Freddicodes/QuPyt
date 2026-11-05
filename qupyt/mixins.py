"""
A shared mixin class to unify
how parameters are updated or set from
a configuration dictionary.
"""

import logging
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class UpdateConfigurationProtocol(Protocol):
    """
    Protocol defining structure of the attribute_map
    for the mixin class.
    """

    attribute_map: dict[str, Callable[[Any], None]]


UpdateConfigurationType = dict[str, Callable[[Any], None]]


# pylint: disable=too-few-public-methods
class ConfigurationMixin:
    """
    Mixin class to create a uniform update method
    for attributes and hardware values from a configuration
    dictionary.
    """

    def _update_from_configuration(
        self: UpdateConfigurationProtocol, configuration: dict[str, Any]
    ) -> None:
        for attr, value in configuration.items():
            try:
                # splitter access to map and call of method as this could hide errors
                setter = self.attribute_map[attr]
            except KeyError:
                raise KeyError(f"Unknown attribute: {attr}") from None
            setter(value)


class PulseSequenceError(Exception):
    "PulseStreamer sequence definition contains non-valid input (amplitude, frequency,...)"


class DAQOpenError(RuntimeError):
    """Raised when the DAQ could not be opened and configured."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "An error occurred while opening and configuring the DAQ. "
            "Please make sure the DAQ is connected and powered on."
        )


class ConfigurationError(ValueError):
    """Error type to be raised for hardware misconfigurations"""

    def __init__(self, config_param: Any, value: Any, valids: Any) -> None:
        self.config_param = config_param
        self.value = value
        self.valids = valids
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        return (
            f"Invalid configuration values!\n"
            f"It seems you tried to configure {self.config_param} as {self.value}\n"
            f"Try using one of the following configuration options instead:\n\n"
            " ===>   "
            f"{self.valids}\n\n"
        )


class UnknownHardwareTypeError(ConfigurationError):
    """A registry was asked for a name that no class registered."""

    kind: ClassVar[str] = "hardware"  # overridden per family

    def __init__(self, value: str, valids: list[str]) -> None:
        super().__init__(f"the {self.kind} type", value, valids)

    def _format_message(self) -> str:
        return f"Unknown {self.kind} type {self.value!r}. Available: {self.valids}"


class UnknownSensorError(UnknownHardwareTypeError):
    """Raised by :meth:`Sensor.create` for an unregistered sensor name."""

    kind = "sensor"


class UnknownSynchroniserError(UnknownHardwareTypeError):
    """Raised by :meth:`Synchroniser.create` for an unregistered synchroniser name."""

    kind = "synchroniser"


class UnknownDeviceError(UnknownHardwareTypeError):
    """Raised by :meth:`Device.create` for an unregistered device name."""

    kind = "device"
