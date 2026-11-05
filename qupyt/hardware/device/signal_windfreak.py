import logging
from typing import Any

import serial
from pydantic import validate_call

from qupyt.utils.decorators import ChannelValue, coerce_device_config_shape, loop_inputs
from qupyt.utils.errors import close_on_initialization_error

from .signal_sources import SignalSource

logger = logging.getLogger(__name__)


class WindFreakSNV(SignalSource, device_types=("WindFreakSNV",)):
    """WindFreak SynthNV over a serial port.

    Single channel; the channel in the configuration is ignored.

    Configuration keys:

    - ``frequency``: in Hz (sent in MHz, rounded to 0.1 MHz).
    - ``amplitude``: raw power register value, sent as ``a<value>`` (0-63).
    - ``power_level``: 1 high, 0 low (set to 1 on creation).
    - ``output_on_off``: 1 on, 0 off.
    """

    @close_on_initialization_error
    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:
        self.address = address
        super().__init__(configuration)
        try:
            self.instance = serial.Serial(self.address, timeout=1)
        except serial.SerialException:
            logger.exception(
                f"Connection to WindFreak on {self.address} failed".ljust(65, ".")
                + "[failed]"
            )
            raise
        logger.info(
            f"Connected to WindFreak on {self.address}".ljust(65, ".") + "[done]"
        )
        self._set_power_level(1)
        self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off

    def __repr__(self) -> str:
        return f"WindFreak(address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type (synth-nv) WindFreak(address: {self.address})"

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        _channel, value = ampl
        self.instance.write(f"a{value}".encode())  # min 0 , max 63
        logger.info("Windfreak set amplitude to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        _channel, value = freq
        value = float(value) / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(value, 1)}".encode())
        logger.info("Windfreak set frequency to [MHz]".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ChannelValue) -> None:
        # High - 1, Low - 0
        _channel, value = power_level
        self.instance.write(f"h{value}".encode())
        logger.info("Windfreak power level set to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ChannelValue) -> None:
        _channel, value = on_off
        self.instance.write(f"o{value}".encode())
        logparam = "[ON]" if value == 1 else "[OFF]"
        logger.info("WindFreak output set".ljust(65, ".") + logparam)

    def close(self) -> None:
        if getattr(self, "instance", None) is None:
            return
        self.instance.close()
        self.instance = None
        logger.info("WindFreak instance closed".ljust(65, ".") + "[done]")


class WindFreakHDM(SignalSource, device_types=("WindFreakHDM",)):
    """WindFreak SynthHD over a serial port, two channels (``channel_0`` / ``channel_1``, selected with ``C<channel>`` before each command).

    Configuration keys:

    - ``frequency``: in Hz (sent in MHz).
    - ``amplitude``: sent as is (``W<value>``).
    - ``power_level``: 1 high, 0 low (set to 1 on creation; not per channel).
    - ``output_on_off``: 1 on, 0 off (not per channel).
    """

    @close_on_initialization_error
    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:
        self.address = address
        super().__init__(configuration)
        self.instance = serial.Serial(self.address, timeout=1)
        self._set_power_level(1)
        self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off

    def __repr__(self) -> str:
        return f"WindFreakHDM(address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type (synth-hd) WindFreakHDM(address: {self.address})"

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        channel, value = ampl
        self.instance.write(f"C{channel}".encode())
        self.instance.write(f"W{value}".encode())  # min 0 , max 63
        logger.info("Windfreak set amplitude to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        channel, value = freq
        self.instance.write(f"C{channel}".encode())
        value = float(value) / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(value, 8)}".encode())
        logger.info("Windfreak set frequency to [MHz]".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ChannelValue) -> None:
        # High - 1, Low - 0
        _channel, value = power_level
        self.instance.write(f"h{value}".encode())
        logger.info("Windfreak power level set to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ChannelValue) -> None:
        _channel, value = on_off
        self.instance.write(f"o{value}".encode())
        logparam = "[ON]" if value == 1 else "[OFF]"
        logger.info("WindFreak output set".ljust(65, ".") + logparam)

    def close(self) -> None:
        if getattr(self, "instance", None) is None:
            return
        self.instance.close()
        self.instance = None
        logger.info("WindFreak instance closed".ljust(65, ".") + "[done]")


class WindFreakOfficial(SignalSource, device_types=("WindFreak",)):
    """WindFreak SynthHD via the ``windfreak`` Python package (registered as ``WindFreak``).

    Channels are 0-based: ``channel_0`` and ``channel_1``; both outputs are
    enabled on creation.

    Configuration keys:

    - ``frequency``: in Hz.
    - ``amplitude``: output power (the package's ``power``, dBm).
    - ``output_on_off``: 1 on, anything else off.
    """

    @close_on_initialization_error
    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:

        try:
            from windfreak import SynthHD
        except ImportError as e:
            raise ImportError(
                "WindFreak needs package 'windfreak'. Install with pip install qupyt[windfreak]"
            ) from e

        self.address = address
        super().__init__(configuration)
        self.instance = SynthHD(self.address)
        self.instance.init()
        # self._set_power_level(1)
        # self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off
        self._set_output_on_off(("channel_0", True))
        self._set_output_on_off(("channel_1", True))

    def __repr__(self) -> str:
        return f"WindFreakOfficial(address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type (windfreak package, SynthHD) WindFreakOfficial(address: {self.address})"

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        channel, value = ampl
        index = int(channel)
        value = float(value)
        self.instance[index].power = value
        logger.info(
            f"Windfreak set amplitude channel{channel} to".ljust(65, ".") + f"{value}"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        channel, value = freq
        index = int(channel)
        value = float(value)
        # might need rouding
        self.instance[index].frequency = value
        logger.info(
            f"Windfreak set channel {channel} frequency to [Hz]".ljust(65, ".")
            + f"{value}"
        )

    # def _set_power_level(self, power_level: ParameterInput) -> None:
    #     # High - 1, Low - 0
    #     _channel, power_level = power_level
    #     self.instance.write(f"h{power_level}".encode())
    #     logger.info("Windfreak power level set to".ljust(
    #         65, ".") + f"{power_level}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ChannelValue) -> None:
        channel, value = on_off
        index = int(channel)
        value = value == 1
        self.instance[index].enable = value
        logparam = "[ON]" if value == 1 else "[OFF]"
        logger.info("WindFreak output set".ljust(65, ".") + logparam)

    def close(self) -> None:
        if getattr(self, "instance", None) is None:
            return
        self.instance.close()
        self.instance = None
        logger.info("WindFreak instance closed".ljust(65, ".") + "[done]")


class WindFreakSHDMini(SignalSource, device_types=("WindFreakSHDMini",)):
    """WindFreak SynthHD mini over a serial port (single channel).

    Configuration keys:

    - ``frequency``: in Hz (sent in MHz).
    - ``amplitude``: in dBm, -13 to 20 (``W<value>``); only takes effect in
      high power mode.
    - ``power_level``: 1 high, 0 low (set to 1 on creation).
    - ``output_on_off``: 1 on, 0 off (``E<value>``).
    """

    @close_on_initialization_error
    def __init__(self, address: str, configuration: dict[str, Any] | None) -> None:
        self.address = address
        super().__init__(configuration)
        self.instance = serial.Serial(self.address, timeout=1)
        self._set_power_level(1)
        self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        channel, value = ampl
        int(channel)  # only validates the channel number (single-channel device)
        value = float(value)
        self.instance.write(f"W{value}".encode())  # min -13.000, max 20.000
        logger.info("Windfreak set amplitude to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        channel, value = freq
        int(channel)  # only validates the channel number (single-channel device)
        value = float(value) / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(value, 8)}".encode())
        logger.info("Windfreak set frequency to [MHz]".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ChannelValue) -> None:
        _channel, value = power_level
        # High - 1, Low - 0;  only in high power mode the output actually changes with the assigned dBm
        self.instance.write(f"h{value}".encode())
        logger.info("Windfreak power level set to".ljust(65, ".") + f"{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ChannelValue) -> None:
        _channel, value = on_off
        self.instance.write(f"E{value}".encode())
        logparam = "[ON]" if value == 1 else "[OFF]"
        logger.info("WindFreak output set".ljust(65, ".") + logparam)

    def __repr__(self) -> str:
        return f"WindFreak SynthHD Mini (address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type WindFreak SynthHD Mini (address: {self.address})"

    def close(self) -> None:
        if getattr(self, "instance", None) is None:
            return
        self.instance.close()
        self.instance = None
        logger.info("WindFreak SynthHD Mini instance closed".ljust(65, ".") + "[done]")
