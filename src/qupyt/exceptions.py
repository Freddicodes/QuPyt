from typing import Any, ClassVar


class ConfigurationError(Exception):
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


class UnknownHardwareTypeError(Exception):
    """A registry was asked for a name that no class registered."""

    kind: ClassVar[str] = "hardware"  # overridden per family

    def __init__(self, value: str, valids: list[str]) -> None:
        self.value: str = value
        self.valids: list[str] = valids

    def __str__(self) -> str:
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


class PulseSequenceError(Exception):
    "PulseStreamer sequence definintion contains non valid input (amplitude, frequency,...)"
