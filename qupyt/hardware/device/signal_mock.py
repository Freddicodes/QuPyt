import logging
from time import sleep
from typing import Any

from pydantic import validate_call

from qupyt.utils.decorators import (
    coerce_device_config_shape,
    loop_inputs,
)
from qupyt.utils.logging import DONE
from qupyt.utils.types import ParameterInput

from .signal_source import SignalSource

logger = logging.getLogger(__name__)


class MockSignalSource(
    SignalSource,
    device_types=("Mock",),
):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
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
    def set_frequency(self, freq: ParameterInput) -> None:
        channel, freq = freq
        sleep(0.1)
        logger.info(
            f"MOCKING! -> set frequency channel {channel} to {freq}", extra=DONE
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ParameterInput) -> None:
        channel, ampl = ampl
        sleep(0.1)
        logger.info(
            f"MOCKING! -> set amplitued channel {channel} to {ampl}", extra=DONE
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ParameterInput) -> None:
        channel, phase = phase
        sleep(0.1)
        logger.info(f"MOCKING! -> set phase channel {channel} to {phase}", extra=DONE)

    def close(self) -> None:
        pass
