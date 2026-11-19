import logging
from typing import Any, Self

from pydantic import validate_call

from qupyt.exceptions import ConfigurationError
from qupyt.hardware.visa_handler import VisaObject
from qupyt.utils.decorators import coerce_device_config_shape, loop_inputs
from qupyt.utils.types import ParameterInput

from .signal_source import SignalSource

logger = logging.getLogger(__name__)


class PhaseMixin:
    """
    Set the output signal phase for the specified channel.
    """

    def __init__(self) -> None:
        self.attribute_map["phase"] = self.set_phase

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ParameterInput) -> None:
        channel, phase = phase
        phase_command = self.command.get(f"SetPhase{channel}")
        if phase_command is None:
            raise ValueError(
                f"The configured signal source {repr(self)} currently does not implement setting a phase."
            )
        self.instance.write(phase_command + str(phase))
        # self.opc_wait()
        logger.info(
            f"{self.s_type} set phase channel {channel} to".ljust(65, ".") + f"{phase}"
        )


class VisaSignalSource(VisaObject, SignalSource):
    """
    SignaSource implementation for devices that implement
    the VISA protocol
    """

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any]
    ) -> None:
        self.address = address
        VisaObject.__init__(self, address, device_type)
        SignalSource.__init__(self, configuration)

    @classmethod
    def _from_config(
        cls, device_type: str, address: str, configuration: dict[str, Any] | None
    ) -> Self:
        return cls(address, device_type, configuration)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ParameterInput) -> None:
        channel, ampl = ampl
        self.instance.write(self.command[f"SetAmpl{channel}"] + str(ampl))
        self.opc_wait()
        logger.info(
            f"{self.s_type} set amplitude channel {channel} to".ljust(65, ".")
            + f"{ampl}"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ParameterInput) -> None:
        channel, freq = freq
        self.instance.write(self.command[f"SetFreq{channel}"] + str(freq))
        self.opc_wait()
        logger.info(
            f"{self.s_type} set frequency channel {channel} to".ljust(65, ".")
            + f"{freq}"
        )


class AFGSignalSource(VisaSignalSource, PhaseMixin, device_types=("TekAFG",)):
    jls_extract_var = (
        """Special class for TekAFG to enable gating and output phase control."""
    )
    jls_extract_var

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any]
    ) -> None:
        VisaSignalSource.__init__(self, address, device_type, configuration)
        PhaseMixin.__init__(self)
        self.attribute_map["gating"] = self._set_gate_mode
        self.attribute_map["phase"] = self.set_phase

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_gate_mode(self, mode: ParameterInput) -> None:
        valids = ["off", "gate"]
        channel, mode = mode
        if channel != "1":
            raise ConfigurationError("TekAFG burst channel", channel, ["channel_1"])
        if mode.lower() not in valids:
            raise ConfigurationError("Burst mode", mode, valids)
        if mode.lower() == "off":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "OFF")
        if mode.lower() == "gate":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
            self.instance.write(self.command[f"SetBurstMode{channel}"] + "GATed")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ParameterInput) -> None:
        channel, phase = phase
        if not (0 <= phase <= 360):
            raise ConfigurationError("Output phase", phase, "range 0 to 360 degrees")

        cmd = self.command[f"SetPhase{channel}"]
        self.instance.write(f"{cmd}{phase} DEG")


class RigolSignalSource(VisaSignalSource, PhaseMixin, device_types=("Rigol",)):
    """Special class for Rigol DG1022 to enable gating and setting the phase in degrees"""

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any]
    ) -> None:
        VisaSignalSource.__init__(self, address, device_type, configuration)
        PhaseMixin.__init__(self)
        self.attribute_map["gating"] = self._set_gate_mode
        self.attribute_map["phase"] = self.set_phase
        self.attribute_map["triggered_n_cycles"] = self._set_triggered_n_cycles

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_gate_mode(self, mode):
        valids = ["off", "gate"]
        channel, mode = mode
        if mode.lower() not in valids:
            raise ConfigurationError("Burst mode", mode, valids)
        if mode.lower() == "off":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "OFF")
        if mode.lower() == "gate":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
            self.instance.write(self.command[f"SetBurstMode{channel}"] + "GAT")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ParameterInput) -> None:
        channel, phase = phase
        if not (-360 <= phase <= 360):
            raise ConfigurationError("Burst phase", phase, "range -360 to 360 degrees")

        cmd = self.command[f"SetBurstPhase{channel}"]
        self.instance.write(f"{cmd}{phase}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_triggered_n_cycles(self, n_cycles: ParameterInput) -> None:
        """
        Configure Rigol DG1000Z burst mode such that every external rising-edge
        trigger produces exactly n_cycles waveform periods.

        Expected config examples:
            triggered_n_cycles: 43
            triggered_n_cycles: ["1", 43]
            triggered_n_cycles: ["channel_1", 43]

        This internally sets:
            - burst ON
            - burst mode TRIG
            - burst cycles = n_cycles

        Trigger source and trigger slope are left unchanged to avoid
        overriding setup-specific instrument configuration.
        """
        channel, n_cycles = n_cycles
        n_cycles = int(n_cycles)

        if n_cycles < 1:
            raise ValueError("triggered_n_cycles must be >= 1")

        self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
        self.instance.write(self.command[f"SetBurstMode{channel}"] + "TRIG")
        self.instance.write(self.command[f"SetBurstNCycles{channel}"] + str(n_cycles))
        self.opc_wait()

        logger.info(
            f"{self.s_type} set triggered N-cycle burst channel {channel} to".ljust(
                65, "."
            )
            + f"{n_cycles} cycles"
        )


class SMAandSMBVisaSignalSource(VisaSignalSource, device_types=("SMA", "SMB")):
    """
    SignaSource implementation for devices that implement
    the VISA protocol and configure a switchable frequency list
    """

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any]
    ) -> None:
        self.slist_frequencies: list[float] = []
        self.slist_amplitudes: list[float] = []
        super().__init__(address, device_type, configuration)
        self.attribute_map["slist_frequencies"] = self._set_slist_frequencies
        self.attribute_map["slist_amplitudes"] = self._set_slist_amplitudes

    def _set_slist_frequencies(self, slist_frequencies: list[float]) -> None:
        self.slist_frequencies = slist_frequencies
        if len(self.slist_frequencies) != 0 and (
            len(self.slist_amplitudes) == len(self.slist_frequencies)
        ):
            self._configure_slist()

    def _set_slist_amplitudes(self, slist_amplitudes: list[float]) -> None:
        self.slist_amplitudes = slist_amplitudes
        if len(self.slist_amplitudes) != 0 and (
            len(self.slist_amplitudes) == len(self.slist_frequencies)
        ):
            self._configure_slist()

    def _configure_slist(self) -> None:
        self.instance.write("*RST")
        self.opc_wait()
        self.instance.write("OUTP:STAT ON")
        self.opc_wait()
        self.instance.write("SOURce:FREQ:MODE CW")
        self.opc_wait()
        # Delete list if exists.
        if "Synclist" in self.instance.query("SOURce:LIST:CAT?"):
            self.instance.write('SOURce:LIST:DEL "Synclist"')
        # create list
        self.instance.write('SOURce:LIST:SEL "Synclist"')
        self.opc_wait()

        # set dwell time
        self.opc_wait()

        # write frequency to list first row in arg first one being the NV
        # second one the overhauser-frequency
        set_slist_frequencies = f"SOURce:LIST:FREQ {self.slist_frequencies[0]} Hz"
        for slist_freq in self.slist_frequencies[1:]:
            set_slist_frequencies += f", {slist_freq} Hz"
        self.instance.write(set_slist_frequencies)
        self.opc_wait()

        # write amp to list first row in arg first one being the NV
        # second one the overhauser-amp
        set_slist_amplitudes = f"SOURce:LIST:POW {self.slist_amplitudes[0]} dBm"
        for slist_ampl in self.slist_amplitudes[1:]:
            set_slist_amplitudes += f", {slist_ampl} dBm"
        self.instance.write(set_slist_amplitudes)
        self.opc_wait()

        # set list mode to step not auto
        self.instance.write("SOURce:LIST:MODE STEP")
        self.opc_wait()
        # set trigger type to external
        self.instance.write("SOURce:LIST:TRIG:SOUR EXT")
        self.opc_wait()
        self.instance.write("SOURce:FREQ:MODE LIST")
        self.opc_wait()
        logger.info("%s[done]", "SMB or SMA set slist values.".ljust(65, "."))


class TekAWGSignalSource(VisaSignalSource, device_types=("TekAWG",)):
    pass


class SRSSignalSource(VisaSignalSource, device_types=("SRS",)):
    pass
