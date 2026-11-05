"""Power supply remote control.

All power supplies share the :class:`PowerSupply` base class, which defines
the configuration keys and their validation. A concrete model only implements
the hardware commands. Currently supported:

- ``KonradKD3000``: Konrad power supplies via a serial connection
  (exact models still to be confirmed).

Power supplies are configured in the measurement YAML file under
``static_devices``::

    static_devices:
      psu:
        device_type: 'KonradKD3000'
        address: '/dev/tty.usbserial-1'   # serial port, e.g. COM3 on Windows
        config:
          output: 'on'

Configuration keys:

- ``output``: ``'on'`` or ``'off'``. Unquoted ``on``/``off``/``yes``/``no``
  (YAML booleans) work as well. If the key is missing, the output is
  switched **off**: the hardware always matches the configuration.

Power supplies belong under ``static_devices``. Dynamic devices only get the
swept (numeric) parameters for each step, so ``output`` would be missing and
the output switched off in every step.
"""

import logging
from abc import abstractmethod
from typing import Any, ClassVar

import serial

from qupyt.mixins import ConfigurationError

from .base import Device

logger = logging.getLogger(__name__)


class PowerSupply(Device):
    """Abstract base class for all power supplies.

    It defines the configuration keys every power supply accepts and how they
    are validated. Concrete models only implement the hardware part
    (:meth:`_apply_output`) and :meth:`~qupyt.hardware.device.base.Device.close`.

    Unlike the base :class:`~qupyt.hardware.device.base.Device`, keys missing
    from the configuration are not left unchanged but set to :attr:`DEFAULTS`,
    so the hardware always matches the configuration.

    Parameters
    ----------
    configuration : dict | None
        Configuration dictionary, see the
        module documentation for the accepted keys.
    """

    #: Applied for every key the configuration does not set
    #: (missing ``output`` = off).
    DEFAULTS: ClassVar[dict[str, Any]] = {"output": "off"}
    #: Accepted values for ``output`` (case-insensitive).
    OUTPUT_STATES: ClassVar[tuple[str, ...]] = ("on", "off")

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        super().__init__(configuration)
        self.attribute_map["output"] = self.set_output

    def set_values(self) -> None:
        """Apply the configuration, with :attr:`DEFAULTS` for every missing key.

        Raises
        ------
        ConfigurationError
            if a value is invalid.
        KeyError
            if the configuration contains an unknown key.
        """
        self._update_from_configuration({**self.DEFAULTS, **(self.configuration or {})})

    def set_output(self, value: str | bool) -> None:
        """Switch the output on or off.

        Parameters
        ----------
        value : str | bool
            ``"on"`` / ``"off"`` (any case), or a bool, which is what
            YAML makes of unquoted on/off/yes/no/true/false.

        Raises
        ------
        ConfigurationError
            for any other value.
        """
        if isinstance(value, bool):
            value = "on" if value else "off"
        if (
            not isinstance(value, str)
            or value.strip().lower() not in self.OUTPUT_STATES
        ):
            raise ConfigurationError(
                "power supply output", value, list(self.OUTPUT_STATES)
            )
        state = value.strip().lower()

        self._apply_output(state == "on")
        logger.info("Turned %s power supply output", state.upper())

    def on(self) -> None:
        """Turn the output on (shortcut for ``set_output("on")``)."""
        self.set_output("on")

    def off(self) -> None:
        """Turn the output off (shortcut for ``set_output("off")``)."""
        self.set_output("off")

    @abstractmethod
    def _apply_output(self, on: bool) -> None:
        """Send the output command to the hardware.

        Called by :meth:`set_output` after validation.

        Parameters
        ----------
        on : bool
            ``True`` to switch the output on, ``False`` to switch it off.
        """


class KonradKD3000(PowerSupply, device_types=("KonradKD3000",)):
    """Konrad power supply controlled via a serial connection (``OUT1`` / ``OUT0`` commands).

    The exact supported models are still to be confirmed.

    Registered as ``'KonradKD3000'``.

    Parameters
    ----------
    address : str
        Serial port of the power supply, e.g.
        ``'/dev/tty.usbserial-1'`` or ``'COM3'``.
    configuration : dict | None
        Configuration dictionary, see
        :class:`PowerSupply`.

    Attributes
    ----------
        - **instance** (serial.Serial): The open serial connection.
    """

    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:
        """Open the serial connection to the power supply.

        Raises
        ------
        serial.SerialException
            if the port cannot be opened.
        """
        super().__init__(configuration)
        self.address: str = address
        try:
            self.instance: serial.Serial = serial.Serial(self.address, timeout=1)
        except ValueError, serial.SerialException:
            logger.exception("Connection to power supply %s failed", address)
            raise
        self._is_open = True
        logger.info(f"Connected to power supply {address}".ljust(65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(address: {self.address})"

    def __str__(self) -> str:
        return f"Power supply of type {type(self).__name__}(address: {self.address})"

    def close(self) -> None:
        """Close the serial connection to the power supply (safe to call twice)."""
        if not getattr(self, "_is_open", False):
            return
        self.instance.close()
        self._is_open = False
        logger.info(f"Closed power supply {self.address}".ljust(65, ".") + "[done]")

    def _apply_output(self, on: bool) -> None:
        """Write ``OUT1`` (on) or ``OUT0`` (off) to the serial port.

        Parameters
        ----------
        on : bool
            ``True`` to switch the output on, ``False`` to switch it off.
        """
        state = "ON" if on else "OFF"
        try:
            self.instance.write(b"OUT1" if on else b"OUT0")
        except Exception:
            logger.exception(
                "Failed to turn %s power supply output at %s", state, self.address
            )
            raise
        logger.info(
            f"Turned {state} power supply output at {self.address}".ljust(65, ".")
            + "[done]"
        )
