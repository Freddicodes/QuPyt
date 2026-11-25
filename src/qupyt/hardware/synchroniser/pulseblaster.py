import ctypes
import logging
import sys
from pathlib import Path
from typing import Any

import yaml
from termcolor import colored

from qupyt.config import get_settings
from qupyt.pulse_sequences.sequence_designer import PulseBlasterSequence
from qupyt.utils.imports import install_hint

from .base import Synchroniser, _validate_synchroniser_sequence_mode

logger = logging.getLogger(__name__)


class PulseBlaster(Synchroniser, synchroniser_types=("PulseBlaster",)):
    """
    Class to represent PulseBlaster card.

    Attributes
    ----------
    int PBclk:
        PulseBlaster clock frequency (in MHz)
    int PB_min_instr_clk_cycles:
        minimum instruction time, in clock periods
    int PB_STARTtrig:
        PB channel bit to DAQ start trigger
    int PB_DAQ:
        PB channel bit to DAQ gate/ sample clock
    int PB_AOM:
        PB channel bit to TTL of AOM driver
    int PB_MW:
        PB channel bit to TTL of microwave switch

    Methods
    -------
    error_catcher(int status):
        Catches error in PB board status
    configure_pb():
        Configures PB board
    int status pb_inst_pbonly(int bit flags, int instruction,
        int instruction_data, int pulse_length):
        Create single instruction for the pulse program
    create_json_sequence(pulseseq_file_name):
        Loads sequence array from npz file and save the decomposed
        data (PB channel bit masks and pulse durations) into json file
    decompose_sequence_array(seq_array, pulseseq_file_name):
        Decomposes seq_array into PB channel bit masks
        and corresponding pulse durations
    decompose_sequence(sequence):
        Decompose single sequence into PB channel
        bit masks and its pulse durations
    get_int_powers_of_two(sequence):
        Finds integer list of powers of 2 for each bit sequence
    load_sequence(pulseseq_file_name):
        Loads Pb channel bit masks and pulse durations from json file
        to pulse program memory
    start_programming():
        Starts the programming of PB pulse program
    stop_programming():
        Stops the programming of PB pulse program
    program_pb(channel_bit_masks, pulse_duration_list):
        Program pulse program memory using
        PB channel bits and pulse durations
    run():
        Triggers/starts the execution of the Pulse Program
    close():
        Closes the communication with the PB board
    stop():
        Stops the execution of the Pulse Program and closes
        the communication. TTL outputs will return to zero.
    """

    def __init__(
        self, configuration: dict[str, Any], channel_mapping: dict[str, Any]
    ) -> None:
        """
        Configure the PB board core clock frequency,
        minimum instruction clock cycle and PB channel connections.

        """
        with install_hint(
            vendor="Install SpinAPI from SpinCore or set SPINAPI_LIBRARY to the library path."
        ):
            import qupyt.hardware.wrappers.spinapi_adapted as spapi

        self._spapi = spapi
        self.device_type: str = "PulseBlaster"
        self.samprate: float = 500  # MHz
        self.pb_min_instr_clk_cycles = 5
        self.channel_mapping = channel_mapping
        Synchroniser.__init__(self, configuration, channel_mapping)
        self.attribute_map["sampling_rate"] = self._set_sampling_rate_attribute
        self.attribute_map["min_instr_clk_cycles"] = self._set_min_instr_clk_cycles
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)

    def __repr__(self) -> str:
        return f"PulseBlaster(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchronizer of type PulseBlaster(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def open(self) -> None:
        self.configure_pb()

    def load_sequence(self, ps_yaml_file: str = "sequence_0.yaml") -> None:
        yaml_file = Path(ps_yaml_file)
        if not yaml_file.is_absolute():
            yaml_file = get_settings().sequence_dir / yaml_file
        with open(yaml_file, "r", encoding="utf-8") as file:
            # PulseBlaster also consumes the expanded program directly. Keep
            # Tek-only upload modes out of this path with a clear error.
            _validate_synchroniser_sequence_mode(
                yaml.safe_load(file) or {}, "PulseBlaster", ("flat",)
            )
        yaml_sequence_transpiler = PulseBlasterSequence(
            self.channel_mapping, ps_yaml_file
        )
        yaml_sequence_transpiler.parse_pulse_sequence_file()
        channel_bit_mask, pulse_duration_list = yaml_sequence_transpiler.compile()
        self.program_pb(channel_bit_mask, pulse_duration_list)

    def close(self) -> None:
        """
        Releases the PulseBlasterESR-PRO board
        and check the current status of PB board.
        """
        # Close the communication with the PB board
        status = self._spapi.pb_close()
        self.error_catcher(status)

    def run(self) -> None:
        """
        Triggers/starts the execution of the Pulse Program
        and closes communication with the board.
        However, Pulse Program execution will continue.
        """
        # Returns a 0 on success or a negative number on an error.
        status = self._spapi.pb_start()
        self.error_catcher(status)
        print(colored("Started execution of Pulseblaster card pulse program!", "green"))

    def trigger(self) -> None:
        status = self._spapi.pb_start()
        self.error_catcher(status)
        print(colored("Started execution of Pulseblaster card pulse program!", "green"))

    def stop(self) -> None:
        """
        Stops the execution of the Pulse Program and closes the
        communication. TTL outputs will return to zero.
        """
        # Returns a 0 on success or a negative number on an error.
        status = self._spapi.pb_stop()
        self.error_catcher(status)
        print(colored("Stopped execution of Pulseblaster card pulse program!", "green"))

    def error_catcher(self, status: int) -> None:
        """
        Checks the status of the PulseBlasterESR.
        PB returns a negative number on an error,
        and 0 or the instruction number on success.
        If error, prints the error message.

        Parameters:
            int status: current status of PB board
        """
        if status < 0:
            print("Error: ", self._spapi.pb_get_error())
            sys.exit()

    def configure_pb(self) -> None:
        """
        Initializes the PulseBlasterESR-PRO board and set
        the core clock frequency of the board.
        """
        # 1 - Enable the spincore log file, appears as log.txt in current working directory
        self._spapi.pb_set_debug(0)
        # Initializing communication with the PulseBlasterESR-PRO board
        status = self._spapi.pb_init()
        # catch error if any and exit the program
        self.error_catcher(status)
        # Configure the core clock frequency (in MHz)
        self._spapi.pb_core_clock(self.samprate)

    def pb_inst_pbonly(self, flags, instruction, instruction_data, pulse_length):
        """
        Create single instruction to send to the pulse program. It returns a negative number on an error, or the instruction number upon success.
        If the function returns -99, an invalid parameter was passed to the function.

        Instruction format:
            int status pb_inst_pbonly(int bit flags, int instruction, int instruction_data, int pulse_length)

        Parameters:
            int bit flags: state of each TTL output bit.
            int instruction: type of instruction is to be executed.
            int instruction_data: data to be used with the instruction field.
            int pulse_length: duration of this pulse program instruction

        Returns:
            int status: current PB board status
        """
        return self._spapi.spinapi.pb_inst_pbonly(
            ctypes.c_uint(flags),
            ctypes.c_int(instruction),
            ctypes.c_int(instruction_data),
            ctypes.c_double(pulse_length),
        )

    def check_pulse_length_short(
        self, pulse_duration: float, current_option: float
    ) -> float:
        if pulse_duration != current_option:
            logger.warning(
                f"pulse duration of {pulse_duration} not possible with PB card. Setting to {current_option}"
            )
            pulse_duration = current_option
            return pulse_duration
        return pulse_duration

    def program_pb(
        self, channel_bit_masks: list[int], pulse_duration_list: list[float]
    ) -> None:
        """
        Program the PB pulse program memory by sending instructypesions
        for each channel bit mask and corresponding pulse duration.
        Channel bit mask can be a decimal, hexadecimal or binary.
        """
        self.start_programming()

        # Send instructypesions to the pulse program
        # Instructypesion format:
        # int status pb_inst_pbonly(int bit flags, int instructypesion,
        # int instructypesion_data, int pulse_length)
        for i, pulse_duration in zip(
            range(len(channel_bit_masks)), pulse_duration_list
        ):
            # All pulse duration checks in mus.
            if pulse_duration >= 0.01:
                channel_bit_mask = channel_bit_masks[i]
            else:
                # Short Pulse Feature:
                # bits 23-21 controls the number of clock periods
                if 0 < pulse_duration <= 0.003:
                    # 001 for 1 clock period, 2ns for 500 MHz
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.002
                    )
                    channel_bit_mask = channel_bit_masks[i] + 2**21
                elif 0.003 < pulse_duration <= 0.005:
                    # 010 for 2 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.004
                    )
                    channel_bit_mask = channel_bit_masks[i] + 2**22
                elif 0.005 < pulse_duration <= 0.007:
                    # 011 for 3 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.006
                    )
                    channel_bit_mask = channel_bit_masks[i] + 2**21 + 2**22
                elif 0.007 < pulse_duration <= 0.009:
                    # 100 for 4 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.008
                    )
                    channel_bit_mask = channel_bit_masks[i] + 2**23
                elif 0.009 < pulse_duration < 0.01:
                    # 100 for 4 clock periods
                    pulse_duration = self.check_pulse_length_short(pulse_duration, 0.01)
                    channel_bit_mask = channel_bit_masks[i]

                # Shortest minimum instructypesion time is 5 clock periods
                # i.e. 10 ns for 500 MHz
                pulse_duration = self.pb_min_instr_clk_cycles

            # Time resolution of PulseBlaster, given by 1/(clock frequency):
            # t_min = 1e3/self.samprate  # in ns
            # upload times as is
            t_min = 1

            # Instructypesions for pulse sequence
            if i == 0:
                start_instr_num = self._spapi.pb_inst_pbonly(
                    channel_bit_mask,
                    self._spapi.Inst.CONTINUE,
                    0,
                    pulse_duration * t_min * self._spapi.us,
                )
                self.error_catcher(start_instr_num)
            elif i != len(channel_bit_masks) - 1:
                status = self._spapi.pb_inst_pbonly(
                    channel_bit_mask,
                    self._spapi.Inst.CONTINUE,
                    0,
                    pulse_duration * t_min * self._spapi.us,
                )
                self.error_catcher(status)
            else:
                status = self._spapi.pb_inst_pbonly(
                    channel_bit_mask,
                    self._spapi.Inst.BRANCH,
                    start_instr_num,
                    pulse_duration * t_min * self._spapi.us,
                )
                self.error_catcher(status)

        status = self._spapi.pb_inst_pbonly(
            0,
            self._spapi.Inst.STOP,
            0,
            self.pb_min_instr_clk_cycles * t_min * self._spapi.us,
        )
        self.error_catcher(status)

        self.stop_programming()
        print(colored("Pulse sequence is loaded to Pulseblaster card!", "green"))

    def start_programming(self) -> None:
        """
        Starts the programming of PB pulse program
        """
        # Returns a 0 on success or a negative number on an error.
        status = self._spapi.pb_start_programming(self._spapi.PULSE_PROGRAM)
        self.error_catcher(status)

    def stop_programming(self) -> None:
        """
        Stops the programming of PB pulse program
        """
        status = (
            self._spapi.pb_stop_programming()
        )  # Returns a 0 on success or a negative number on an error.
        self.error_catcher(status)

    def _set_sampling_rate_attribute(self, samprate: int) -> None:
        self.samprate = samprate

    def _set_min_instr_clk_cycles(self, clk_cycles: int) -> None:
        # Minimum instructypesion time, in clock periods
        # (5 clk cycles or 10 ns for 500 MHz)
        self.pb_min_instr_clk_cycles = clk_cycles
