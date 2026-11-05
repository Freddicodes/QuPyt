import logging
from time import sleep
from typing import Any

from pydantic import validate_call

from qupyt.utils.decorators import ChannelValue, coerce_device_config_shape, loop_inputs

from .signal_sources import SignalSource

logger = logging.getLogger(__name__)

class MockSignalSource(SignalSource, device_types=("Mock",)):
    """
    Signal source without hardware: ``frequency``, ``amplitude`` and
    ``phase`` are only logged (``MOCKING! -> ...``), with a 0.1 s delay each.
    """

    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:
        self.address = address
        super().__init__(configuration)
        self.attribute_map["phase"] = self.set_phase

    def __repr__(self) -> str:
        return f"MockSignalSource(address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type MockSignalSource(address: {self.address})"

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        channel, value = freq
        sleep(0.1)
        logger.info(
            f"MOCKING! -> set frequency channel {channel} to".ljust(65, ".") + f"{value}"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        channel, value = ampl
        sleep(0.1)
        logger.info(
            f"MOCKING! -> set amplitude channel {channel} to".ljust(65, ".") + f"{value}"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ChannelValue) -> None:
        channel, value = phase
        sleep(0.1)
        logger.info(
            f"MOCKING! -> set phase channel {channel} to".ljust(65, ".") + f"{value}"
        )

    def close(self) -> None:
        pass