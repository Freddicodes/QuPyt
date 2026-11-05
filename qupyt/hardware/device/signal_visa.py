from __future__ import annotations

import logging
from time import sleep
from typing import Any, ClassVar

from pydantic import validate_call

from qupyt.hardware.visa_handler import VisaObject
from qupyt.mixins import ConfigurationError
from qupyt.utils.decorators import ChannelValue, coerce_device_config_shape, loop_inputs

from .signal_sources import PhaseMixin, SignalSource

logger = logging.getLogger(__name__)

class VisaSignalSource(
    VisaObject, SignalSource, device_types=()
):
    """
    SignaSource implementation for devices that implement
    the VISA protocol
    """

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any] | None
    ) -> None:
        self.address = address
        VisaObject.__init__(self, address, device_type)
        SignalSource.__init__(self, configuration)

    @classmethod
    def _from_config(
        cls, device_type: str, address: str, configuration: dict[str, Any] | None
    ) -> VisaSignalSource:
        return cls(address, device_type, configuration)

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_amplitude(self, ampl: ChannelValue) -> None:
        """
        Send ``SetAmpl<channel>`` + value and wait for OPC. No unit is sent,
        so the instrument's default unit for that command applies (see its
        manual).
        """
        channel, value = ampl
        self.instance.write(self.command[f"SetAmpl{channel}"] + str(value))
        self.opc_wait()
        logger.info(
            f"{self.s_type} set amplitude channel {channel} to".ljust(65, ".")
            + f"{value}"
        )

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        """Send ``SetFreq<channel>`` + value and wait for OPC (no unit sent; SCPI default is Hz)."""
        channel, value = freq
        self.instance.write(self.command[f"SetFreq{channel}"] + str(value))
        self.opc_wait()
        logger.info(
            f"{self.s_type} set frequency channel {channel} to".ljust(65, ".")
            + f"{value}"
        )

class SRSSignalSource(VisaSignalSource, device_types=("SRS",)):
    """Stanford Research Systems signal generator (single channel)."""

    COMMANDS: ClassVar[dict[str, str]] = {
        "SetAmpl1": "AMPR ",
        "GetAmpl1": "AMPR?",
        "SetFreq1": "FREQ ",
        "GetFreq1": "FREQ?",
        "OPC": "*OPC?",
    }


class TekAWG(VisaSignalSource, device_types=("TekAWG",)):
    """
    Tektronix AWG as a device. Placeholder: only opens and closes the
    connection, uses the default ``COMMANDS`` (OPC only), so ``frequency``
    and ``amplitude`` are not supported yet. Use the AWG as a synchroniser
    for now.
    """

    def close(self) -> None:
        logger.info("Sleeping for 5 seconds in close to prevent TCPIP issues")
        sleep(5)
        super().close()


class AFGSignalSource(VisaSignalSource, PhaseMixin, device_types=("TekAFG",)):
    """Special class for TekAFG to enable gating and output phase control."""

    COMMANDS: ClassVar[dict[str, str]] = {
        "SetAmpl1": "SOURce1:VOLTage:LEVel:IMMediate:AMPLitude ",
        "GetAmpl1": "SOURce1:VOLTage:LEVel:IMMediate:AMPLitude?",
        "SetFreq1": "SOURce1:FREQuency:FIXed ",
        "GetFreq1": "SOURce1:FREQuency:FIXed?",
        "SetPhase1": "SOURce1:PHASe:ADJust ",
        "SetBurstMode1": "SOURce1:BURSt:MODE ",
        "SetBurstState1": "SOURce1:BURSt:STATe ",
        "SetAmpl2": "SOURce2:VOLTage:LEVel:IMMediate:AMPLitude ",
        "SetFreq2": "SOURce2:FREQuency:FIXed ",
        "SetPhase2": "SOURce2:PHASe:ADJust ",
        # The Tek AFG does not implement an OPC.
        # We therefore skip the waiting time and
        # Query impedance which will alwasy return
        # Non zeros numbers.
        "OPC": "OUTPut1:IMPedance?",
    }

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any] | None
    ) -> None:
        VisaSignalSource.__init__(self, address, device_type, configuration)
        PhaseMixin.__init__(self)
        self.attribute_map["gating"] = self._set_gate_mode
        self.attribute_map["phase"] = self.set_phase

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_gate_mode(self, mode: ChannelValue) -> None:
        valids = ["off", "gate"]
        channel, value = mode
        if channel != "1":
            raise ConfigurationError("TekAFG burst channel", channel, ["channel_1"])
        # str(): a numeric value is rejected below instead of raising AttributeError.
        mode_name = str(value).lower()
        if mode_name not in valids:
            raise ConfigurationError("Burst mode", value, valids)
        if mode_name == "off":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "OFF")
        if mode_name == "gate":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
            self.instance.write(self.command[f"SetBurstMode{channel}"] + "GATed")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ChannelValue) -> None:
        channel, value = phase
        if isinstance(value, str) or not (0 <= value <= 360):
            raise ConfigurationError("Output phase", value, "range 0 to 360 degrees")

        cmd = self.command[f"SetPhase{channel}"]
        self.instance.write(f"{cmd}{value} DEG")


class RigolSignalSource(VisaSignalSource, PhaseMixin, device_types=("Rigol",)):
    """Special class for Rigol DG1022 to enable gating and setting the phase in degrees"""

    COMMANDS: ClassVar[dict[str, str]] = {
        "OPC": "*OPC?",
        "GetAmpl1": "VOLT?",
        "SetAmpl1": "VOLT ",
        "SetPhase1": "BURS:PHAS ",
        "GetPhase1": "BURS:PHAS?",
        "GetFreq1": "FREQ?",
        "SetFreq1": "FREQ ",
        "GetBurstNCycles1": "BURS:NCYC?",
        "SetBurstNCycles1": "BURS:NCYC ",
        "SetBurstMode1": "BURS:MODE ",
        "SetBurstState1": "BURS:STAT ",
        "SetBurstPhase1": ":SOUR1:BURSt:PHASe ",
        "GetBurstMode1": "BURS:MODE?",
        "Outp1": "OUTP ",
        "GetOutp1": "OUTP?",
        "GetAmpl2": "SOUR2:VOLT?",
        "SetAmpl2": "SOUR2:VOLT ",
        "SetPhase2": "SOUR2:BURS:PHAS ",
        "GetPhase2": "SOUR2:BURS:PHAS?",
        "GetFreq2": "SOUR2:FREQ?",
        "SetFreq2": "SOUR2:FREQ ",
        "GetBurstNCycles2": "SOUR2:BURS:NCYC?",
        "SetBurstNCycles2": "SOUR2:BURS:NCYC ",
        "SetBurstMode2": "SOUR2:BURS:MODE ",
        "SetBurstState2": "SOUR2:BURS:STAT ",
        "SetBurstPhase2": ":SOUR2:BURSt:PHASe ",
        "GetBurstMode2": "SOUR2:BURS:MODE?",
        "Outp2": "OUTP2 ",
        "GetOutp2": "OUTP2?",
    }

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any] | None
    ) -> None:
        VisaSignalSource.__init__(self, address, device_type, configuration)
        PhaseMixin.__init__(self)
        self.attribute_map["gating"] = self._set_gate_mode
        self.attribute_map["phase"] = self.set_phase
        self.attribute_map["triggered_n_cycles"] = self._set_triggered_n_cycles

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_gate_mode(self, mode: ChannelValue) -> None:
        valids = ["off", "gate"]
        channel, value = mode
        # str(): a numeric value is rejected below instead of raising AttributeError.
        mode_name = str(value).lower()
        if mode_name not in valids:
            raise ConfigurationError("Burst mode", value, valids)
        if mode_name == "off":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "OFF")
        if mode_name == "gate":
            self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
            self.instance.write(self.command[f"SetBurstMode{channel}"] + "GAT")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_phase(self, phase: ChannelValue) -> None:
        channel, value = phase
        if isinstance(value, str) or not (-360 <= value <= 360):
            raise ConfigurationError("Burst phase", value, "range -360 to 360 degrees")

        cmd = self.command[f"SetBurstPhase{channel}"]
        self.instance.write(f"{cmd}{value}")

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def _set_triggered_n_cycles(self, n_cycles: ChannelValue) -> None:
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
        channel, value = n_cycles
        value = int(value)

        if value < 1:
            raise ValueError("triggered_n_cycles must be >= 1")

        self.instance.write(self.command[f"SetBurstState{channel}"] + "ON")
        self.instance.write(self.command[f"SetBurstMode{channel}"] + "TRIG")
        self.instance.write(self.command[f"SetBurstNCycles{channel}"] + str(value))
        self.opc_wait()

        logger.info(
            f"{self.s_type} set triggered N-cycle burst channel {channel} to".ljust(
                65, "."
            )
            + f"{value} cycles"
        )


class SMAandSMBVisaSignalSource(VisaSignalSource, device_types=("SMA", "SMB")):
    """
    SignaSource implementation for devices that implement
    the VISA protocol and configure a switchable frequency list
    """

    COMMANDS: ClassVar[dict[str, str]] = {
        "SetAmpl1": "POW ",
        "GetAmpl1": "POW?",
        "SetFreq1": "FREQ ",
        "GetFreq1": "FREQ?",
        "OPC": "*OPC?",
    }

    def __init__(
        self, address: str, device_type: str, configuration: dict[str, Any] | None
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
        if "SyncList" in self.instance.query("SOURce:LIST:CAT?"):
            self.instance.write('SOURce:LIST:DEL "SyncList"')
        # create list
        self.instance.write('SOURce:LIST:SEL "SyncList"')
        self.opc_wait()
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
