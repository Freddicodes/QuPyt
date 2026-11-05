from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from qupyt.pulse_sequences.sequence_designer import PulseBlasterSequence

from ._awg_common import _validate_synchroniser_sequence_mode
from .base import Synchroniser

logger = logging.getLogger(__name__)


class PulseBlaster(Synchroniser, synchroniser_types=("PulseBlaster",)):
    """
    SpinCore PulseBlaster card (e.g. PulseBlasterESR-PRO) as a synchroniser.

    The pulse sequence YAML is compiled by
    :class:`~qupyt.pulse_sequences.sequence_designer.PulseBlasterSequence`
    into one instruction per segment (channel bit mask + duration) and
    written to the pulse program memory. The last segment branches back to
    the first, so the program repeats until :meth:`stop`. Only the
    ``flat`` sequence mode is supported.

    Requires the SpinCore SpinAPI library (see
    :mod:`qupyt.hardware.wrappers.spinapi_adapted`).

    :param configuration: Configuration parameters (may be ``None``):

        - ``address`` (str): stored, not used to select a board.
        - ``sampling_rate`` (float): core clock in MHz, default ``500``.
        - ``min_instr_clk_cycles`` (int): shortest instruction in clock
          periods, default ``5`` (10 ns at 500 MHz).
    :type configuration: dict | None
    :param channel_mapping: Channel name → PulseBlaster output bit, e.g.
        ``{"LASER": 0, "MW": 1}``. A channel's flag is ``2 ** bit``.
    :type channel_mapping: dict[str, int]
    :raises ImportError: if the SpinAPI library cannot be loaded.
    """

    def __init__(
        self, configuration: dict[str, Any] | None, channel_mapping: dict[str, int]
    ) -> None:
        """
        Set the defaults and apply ``configuration``. The board itself is
        only initialised in :meth:`open`.
        """
        try:
            # pylint: disable-next=import-outside-toplevel
            import qupyt.hardware.wrappers.spinapi_adapted as spapi
        except ImportError as e:
            raise ImportError(
                "PulseBlaster needs the SpinCore spinapi driver to be installed and functional"
            ) from e
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
        return f"Synchroniser of type PulseBlaster(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def open(self) -> None:
        self.configure_pb()

    def load_sequence(self, ps_yaml_file: Path) -> None:
        with open(ps_yaml_file, "r", encoding="utf-8") as file:
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
        End communication with the board (``pb_close``). A program that is
        still running keeps running.

        :raises RuntimeError: if SpinAPI reports an error.
        """
        status = self._spapi.pb_close()
        self.error_catcher(status)

    def run(self) -> None:
        """
        Triggers/starts the execution of the Pulse Program.
        """
        # Returns a 0 on success or a negative number on an error.
        status = self._spapi.pb_start()
        self.error_catcher(status)
        logger.info("Started PulseBlaster pulse program".ljust(65, ".") + "[done]")

    def trigger(self) -> None:
        """
        The PulseBlaster has no separate software trigger; starting the
        program again plays the sequence once.
        """
        self.run()

    def stop(self) -> None:
        """
        Stop the pulse program (``pb_stop``). The connection stays open;
        see :meth:`close`. Depending on the board, TTL outputs keep their
        state or return to zero.

        :raises RuntimeError: if SpinAPI reports an error.
        """
        # Returns a 0 on success or a negative number on an error.
        status = self._spapi.pb_stop()
        self.error_catcher(status)
        logger.info("Stopped PulseBlaster pulse program".ljust(65, ".") + "[done]")

    def error_catcher(self, status: int) -> None:
        """
        Raise if a SpinAPI call failed. SpinAPI returns a negative number on
        an error and 0 (or the instruction number) on success.

        :param status: Return value of a SpinAPI call.
        :type status: int
        :raises RuntimeError: with SpinAPI's error message if ``status < 0``.
        """
        if status < 0:
            raise RuntimeError(f"PulseBlaster error: {self._spapi.pb_get_error()}")

    def configure_pb(self) -> None:
        """
        Initialise the board and tell SpinAPI the core clock frequency
        (``sampling_rate``).

        :raises RuntimeError: if ``pb_init`` fails.
        """
        # 0: no SpinAPI debug log (1 would write log.txt to the working directory)
        self._spapi.pb_set_debug(0)
        status = self._spapi.pb_init()
        # catch error if any and exit the program
        self.error_catcher(status)
        # Configure the core clock frequency (in MHz)
        self._spapi.pb_core_clock(self.samprate)

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
        Write the pulse program: one instruction per segment, the last one
        branches back to the first, followed by a STOP instruction.

        Segments shorter than 10 ns use the short-pulse feature (flag bits
        21-23, 1-4 clock periods); unsupported lengths are rounded with a
        warning.

        :param channel_bit_masks: Output flags per segment (sum of ``2 ** bit``).
        :type channel_bit_masks: list[int]
        :param pulse_duration_list: Segment durations in µs.
        :type pulse_duration_list: list[float]
        :raises ValueError: if the two lists differ in length.
        :raises RuntimeError: if SpinAPI rejects an instruction.
        """
        self.start_programming()

        # Send instructions to the pulse program
        # Instruction format:
        # int status pb_inst_pbonly(int bit flags, int instruction,
        # int instruction_data, int pulse_length)
        for i, (mask, pulse_duration) in enumerate(
            zip(channel_bit_masks, pulse_duration_list, strict=True)
        ):
            # All pulse duration checks in mus.
            if pulse_duration >= 0.01:
                channel_bit_mask = mask
            else:
                # Short Pulse Feature:
                # bits 23-21 controls the number of clock periods
                if 0 < pulse_duration <= 0.003:
                    # 001 for 1 clock period, 2ns for 500 MHz
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.002
                    )
                    channel_bit_mask = mask + 2**21
                elif 0.003 < pulse_duration <= 0.005:
                    # 010 for 2 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.004
                    )
                    channel_bit_mask = mask + 2**22
                elif 0.005 < pulse_duration <= 0.007:
                    # 011 for 3 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.006
                    )
                    channel_bit_mask = mask + 2**21 + 2**22
                elif 0.007 < pulse_duration <= 0.009:
                    # 100 for 4 clock periods
                    pulse_duration = self.check_pulse_length_short(
                        pulse_duration, 0.008
                    )
                    channel_bit_mask = mask + 2**23
                elif 0.009 < pulse_duration < 0.01:
                    # rounded up to 10 ns: a normal instruction, no short-pulse bits
                    pulse_duration = self.check_pulse_length_short(pulse_duration, 0.01)
                    channel_bit_mask = mask

                # Shortest minimum instruction time is 5 clock periods
                # i.e. 10 ns for 500 MHz
                pulse_duration = self.pb_min_instr_clk_cycles

            # Time resolution of PulseBlaster, given by 1/(clock frequency):
            # t_min = 1e3/self.samprate  # in ns
            # upload times as is
            t_min = 1

            # Instructions for pulse sequence
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
        logger.info("Loaded pulse sequence to PulseBlaster".ljust(65, ".") + "[done]")

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

    def _set_sampling_rate_attribute(self, samprate: float) -> None:
        self.samprate = samprate

    def _set_min_instr_clk_cycles(self, clk_cycles: int) -> None:
        # Minimum instruction time, in clock periods
        # (5 clk cycles or 10 ns for 500 MHz)
        self.pb_min_instr_clk_cycles = clk_cycles
