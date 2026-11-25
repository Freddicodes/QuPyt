import logging
from typing import Any

from pydantic import validate_call

from qupyt.utils.decorators import coerce_device_config_shape, loop_inputs
from qupyt.utils.imports import install_hint
from qupyt.utils.logging import DONE, FAILED
from qupyt.utils.types import ChannelValue, ParameterInput

from .signal_source import SignalSource

logger = logging.getLogger(__name__)


class WindFreakSNV(SignalSource, device_types=("WindFreakSNV",)):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        with install_hint("windfreak"):
            import serial

        self.address: str = address
        super().__init__(configuration)
        try:
            self.instance: serial.Serial = serial.Serial(self.address, timeout=1)
            logger.info(f"Connected to WindFreak on {address}", extra=DONE)
        except serial.SerialException:
            logger.error(f"Connection to WindFreak on {address} failed", extra=FAILED)
            raise

        self._set_power_level(power_level=1)
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
        _channel, ampl = ampl
        self.instance.write(f"a{ampl}".encode())  # min 0 , max 63
        logger.info(f"Windfreak set amplitude to {ampl}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ParameterInput) -> None:
        _channel, freq = freq
        freq = freq / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(freq, 1)}".encode())
        logger.info(f"Windfreak set frequency to [MHz] {freq}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ParameterInput) -> None:
        # High - 1, Low - 0
        _channel, power_level = power_level
        self.instance.write(f"h{power_level}".encode())
        logger.info(f"Windfreak power level set to {power_level}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ParameterInput) -> None:
        _channel, on_off = on_off
        self.instance.write(f"o{on_off}".encode())
        logparam = "ON" if on_off == 1 else "OFF"
        logger.info(f"WindFreak output set {logparam}", extra=DONE)

    def close(self) -> None:
        self.instance.close()
        logger.info("WindFreak instance closed", extra=DONE)


class WindFreakHDM(SignalSource, device_types=("WindFreakHDM",)):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        with install_hint("windfreak"):
            import serial

        self.address: str = address
        super().__init__(configuration)
        self.instance: serial.Serial = serial.Serial(self.address, timeout=1)
        self._set_power_level(power_level=1)
        self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off

    def __repr__(self) -> str:
        return f"WindFreakHDM(address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type (synth-hd) WindFreakHDM(address: {self.address})"

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ParameterInput) -> None:
        channel, ampl = ampl
        self.instance.write(f"C{channel}".encode())
        self.instance.write(f"W{ampl}".encode())  # min 0 , max 63
        logger.info(f"Windfreak set amplitude to {ampl}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ParameterInput) -> None:
        channel, freq = freq
        self.instance.write(f"C{channel}".encode())
        freq = freq / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(freq, 8)}".encode())
        logger.info(f"Windfreak set frequency to [MHz] {freq}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ParameterInput) -> None:
        # High - 1, Low - 0
        _channel, power_level = power_level
        self.instance.write(f"h{power_level}".encode())
        logger.info(f"Windfreak power level set to {power_level}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ParameterInput) -> None:
        _channel, on_off = on_off
        self.instance.write(f"o{on_off}".encode())
        logparam = "ON" if on_off == 1 else "OFF"
        logger.info(f"WindFreak output set {logparam}", extra=DONE)

    def close(self) -> None:
        self.instance.close()
        logger.info("WindFreak instance closed", extra=DONE)


class WindFreakOfficial(SignalSource, device_types=("WindFreak",)):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        with install_hint("windfreak"):
            from windfreak import SynthHD

        self.address = address
        super().__init__(configuration)
        self.instance = SynthHD(self.address)
        self.instance.init()
        # self._set_power_level(1)
        # self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off
        self._set_output_on_off(["channel_0", True])
        self._set_output_on_off(["channel_1", True])

    def __repr__(self) -> str:
        return f"WindFreakMini(address: {self.address})"

    def __str__(self) -> str:
        return (
            f"Signal source of type (synth-mini) WindFreakMini(address: {self.address})"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ParameterInput) -> None:
        channel, ampl = ampl
        channel = int(channel)
        ampl = float(ampl)
        self.instance[channel].power = ampl
        logger.info(f"Windfreak set amplitude channel{channel} to {ampl}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ParameterInput) -> None:
        channel, freq = freq
        channel = int(channel)
        freq = float(freq)
        # might need rouding
        self.instance[channel].frequency = freq
        logger.info(
            f"Windfreak set channel {channel} frequency to [Hz] {freq}", extra=DONE
        )

    # def _set_power_level(self, power_level: ParameterInput) -> None:
    #     # High - 1, Low - 0
    #     _channel, power_level = power_level
    #     self.instance.write(f"h{power_level}".encode())
    #     logger.info(f"Windfreak power level set to {power_level}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ParameterInput) -> None:
        channel, on_off = on_off
        channel = int(channel)
        on_off = on_off == 1
        self.instance[channel].enable = on_off
        logparam = "ON" if on_off == 1 else "OFF"
        logger.info(f"WindFreak output set {logparam}", extra=DONE)

    def close(self) -> None:
        self.instance.close()
        logger.info("WindFreak instance closed", extra=DONE)


class WindFreakSHDMini(SignalSource, device_types=("WindFreakSHDMini",)):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        with install_hint("windfreak"):
            import serial

        self.address = address
        super().__init__(configuration)
        self.instance = serial.Serial(self.address, timeout=1)
        self._set_power_level(1)
        self.attribute_map["power_level"] = self._set_power_level
        self.attribute_map["output_on_off"] = self._set_output_on_off

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ParameterInput) -> None:
        channel, ampl = ampl
        channel = int(channel)
        ampl = float(ampl)
        self.instance.write(f"W{ampl}".encode())  # min -13.000, max 20.000
        logger.info(f"Windfreak set amplitude to {ampl}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ParameterInput) -> None:
        channel, freq = freq
        channel = int(channel)
        freq = float(freq)
        freq = freq / 1.0e6  # convert to MHz
        self.instance.write(f"f{round(freq, 8)}".encode())
        logger.info(f"Windfreak set frequency to [MHz] {freq}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_power_level(self, power_level: ParameterInput) -> None:
        _channel, power_level = power_level
        # High - 1, Low - 0;  only in high power mode the output actually changes with the assigned dBm
        self.instance.write(f"h{power_level}".encode())
        logger.info(f"Windfreak power level set to {power_level}", extra=DONE)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_output_on_off(self, on_off: ParameterInput) -> None:
        _channel, on_off = on_off
        self.instance.write(f"E{on_off}".encode())
        logparam = "ON" if on_off == 1 else "OFF"
        logger.info(f"WindFreak output set {logparam}", extra=DONE)

    def __repr__(self) -> str:
        return f"WindFreak SynthHD Mini (address: {self.address})"

    def __str__(self) -> str:
        return f"Signal source of type WindFreak SynthHD Mini (address: {self.address})"

    def close(self) -> None:
        self.instance.close()
        logger.info("WindFreak SynthHD Mini instance closed", extra=DONE)
