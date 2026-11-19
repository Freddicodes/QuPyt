from abc import abstractmethod
from typing import Any

from qupyt.utils.types import ParameterInput

from .base import Device


class SignalSource(Device):
    """Base class of all signal sources (microwave / RF generators, function generators).

    Adds the ``frequency`` and ``amplitude`` configuration keys.

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
        Set Signal Sources to implement a way to set its output frequency.
        """

    @abstractmethod
    def set_amplitude(self, ampl: ParameterInput) -> None:
        """
        Force all Signal Sources to implement a way to
        set its output amplitude.
        Not all devices use the same units. However, wherever practically
        possible amplitudes will be interpreted in units of dBm.
        """
