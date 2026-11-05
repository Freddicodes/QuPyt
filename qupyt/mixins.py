"""Update device attributes and hardware settings from configuration dictionaries."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, ClassVar, Protocol, TypeAlias

logger = logging.getLogger(__name__)


# pylint: disable=too-few-public-methods
class UpdateConfigurationProtocol(Protocol):
    """Protocol for objects exposing a configuration attribute map.

    Attributes
    ----------
    attribute_map : dict[str, Callable[[Any], None]]
        Mapping from configuration keys to their setter functions.
    """

    attribute_map: dict[str, Callable[[Any], None]]


UpdateConfigurationType: TypeAlias = dict[str, Callable[[Any], None]]


# pylint: disable=too-few-public-methods
class ConfigurationMixin:
    """Mixin for applying configuration values through an attribute map."""

    attribute_map: UpdateConfigurationType

    def _update_from_configuration(
        self: UpdateConfigurationProtocol, configuration: dict[str, Any]
    ) -> None:
        for attr, value in configuration.items():
            try:
                # Separate lookup and call: a KeyError raised inside a setter
                # must not be reported as an unknown attribute.
                setter = self.attribute_map[attr]
            except KeyError:
                logger.error(
                    "Unknown configuration attribute %s for %s",
                    attr,
                    type(self).__name__,
                )
                raise KeyError(f"Unknown attribute: {attr}") from None
            logger.debug("Configuring %s.%s=%r", type(self).__name__, attr, value)
            try:
                setter(value)
            except Exception:
                logger.exception(
                    "Failed to configure %s.%s=%r", type(self).__name__, attr, value
                )
                raise


class PulseSequenceError(Exception):
    """Error raised for invalid pulse-sequence values."""


class DAQOpenError(RuntimeError):
    """Raised when the DAQ could not be opened and configured."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "An error occurred while opening and configuring the DAQ. "
            "Please make sure the DAQ is connected and powered on."
        )


class ConfigurationError(ValueError):
    """Error raised for an unsupported hardware configuration value."""

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
