# pylint: disable=logging-format-interpolation
# pylint: disable=logging-not-lazy
"""
Power Supply remote control.
Currently only the Konrad KD300/6000 are supported.

Configuration and use through a YAML config file
is not currently supported but will be added soon.
"""

import logging
from abc import abstractmethod
from typing import Any

import serial

from .base import Device

logger = logging.getLogger(__name__)


class PowerSupply(Device):
    def __init__(self, configuration: dict[str, Any] | None) -> None:
        super().__init__(configuration)
        self.attribute_map["output"] = self.set_output

    def set_output(self, on: bool) -> None:
        """
        Set the output of the powersupply.
        """
        if on:
            self._turn_on()
        else:
            self._turn_off()

    @abstractmethod
    def _turn_on(self) -> None:
        """Turn the output on"""

    @abstractmethod
    def _turn_off(self) -> None:
        """Turn the output off"""


class KonradKD300060000(PowerSupply, device_types=("KonradKD3000", "KonradKD6000")):
    """
    Konrad power supply controlled via a serial connection.

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
        logger.info(f"Connected to power supply {address}".ljust(65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"{type(self).__name__} (address: {self.address})"

    def __str__(self) -> str:
        return f"Power supply of type {type(self).__name__} (address: {self.address})"

    def _turn_off(self) -> None:
        self.instance.write(b"OUT1")

    def _turn_on(self) -> None:
        self.instance.write(b"OUT0")
