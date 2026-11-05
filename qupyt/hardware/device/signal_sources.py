"""
Create all controlls for microwave sources.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any

from pydantic import validate_call
from pyvisa.resources import MessageBasedResource

# ParameterInput is re-exported here for existing imports; it is defined in
# qupyt.utils.decorators next to the decorators that produce it.
from qupyt.utils.decorators import ChannelValue, coerce_device_config_shape, loop_inputs
from qupyt.utils.decorators import ParameterInput as ParameterInput

from qupyt.mixins import UpdateConfigurationType

from .base import Device

logger = logging.getLogger(__name__)

class SignalSource(Device):
    """
    Base class of all signal sources (microwave / RF generators, function
    generators). Adds the ``frequency`` and ``amplitude`` configuration keys.

    Setter values may be given as a scalar (channel 1), a ``(channel, value)``
    pair or a list of pairs; channels are written ``"channel_<n>"``. See
    :mod:`qupyt.utils.decorators`.
    """

    def __init__(self, configuration: dict[str, Any] | None) -> None:
        super().__init__(configuration)
        self.attribute_map["frequency"] = self.set_frequency
        self.attribute_map["amplitude"] = self.set_amplitude

    @abstractmethod
    def set_frequency(self, freq: ParameterInput) -> None:
        """
        Force all Signal Sources to implement a way to
        set its output frequency.
        """

    @abstractmethod
    def set_amplitude(self, ampl: ParameterInput) -> None:
        """
        Force all Signal Sources to implement a way to
        set its output amplitude.
        Not all devices use the same units. However, wherever practically
        possible amplitudes will be interpreted in units of dBm.
        """

class PhaseMixin:
    """
    Set the output signal phase for the specified channel.

    The using class (a VISA signal source) has to provide the attributes
    declared below.
    """

    attribute_map: UpdateConfigurationType
    command: dict[str, str]
    instance: MessageBasedResource
    s_type: str

    def __init__(self) -> None:
        self.attribute_map["phase"] = self.set_phase

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ChannelValue) -> None:
        channel, value = phase
        phase_command = self.command.get(f"SetPhase{channel}")
        if phase_command is None:
            raise ValueError(
                f"The configured signal source {self!r} currently does not implement setting a phase."
            )
        self.instance.write(phase_command + str(value))
        # self.opc_wait()
        logger.info(
            f"{self.s_type} set phase channel {channel} to".ljust(65, ".") + f"{value}"
        )
