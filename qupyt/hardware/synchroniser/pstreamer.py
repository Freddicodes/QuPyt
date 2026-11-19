import logging
from typing import Any

import numpy as np
import yaml
from pulsestreamer import (
    OutputState,
    PulseStreamer,
    Sequence,
    TriggerRearm,
    TriggerStart,
    findPulseStreamers,
)

from qupyt import set_up
from qupyt.exceptions import PulseSequenceError

from .base import Synchroniser, _validate_synchroniser_sequence_mode

logger = logging.getLogger(__name__)


class PStreamer(Synchroniser, synchroniser_types=("SwabInstPS",)):
    def __init__(
        self, configuration: dict[str, Any], channel_mapping: dict[str, Any]
    ) -> None:
        super().__init__(configuration, channel_mapping)
        self.channel_mapping = channel_mapping
        self._update_from_configuration(configuration)
        self.initial_configuration_dict = configuration
        if configuration["address"] == "None":
            self._find_pulse_streamers()

    def __repr__(self) -> str:
        return f"PStreamer(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchronizer of type PStreamer(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def _find_pulse_streamers(self) -> None:
        devices = findPulseStreamers()
        if devices:
            print("Detected PulseStreamer: ")
            print(devices)
            print("----------------------------------------------------\n")
            self.address = devices[0][0]
        else:
            print("No PulseStreamers found by autodetect.\n-------------------\n")
            logger.info("Pulse Streamer not found".ljust(65, ".") + "[failed]")

    def open(self) -> None:
        """
        Opens the connection with the given device of IP = ip_address.
        If an IP is not introduced -- search a device in the network.
        If an IP is introduced -- checks if correct and tries to connect.
        """
        # if no IP adress is provided, try to detect one:
        try:
            self.pulser = PulseStreamer(self.address)
            logger.info(
                f"Pulse Streamer connected at {self.address}".ljust(65, ".") + "[done]"
            )
            print("Connected.\n-------------------------------------------\n")
        except AssertionError:
            logger.exception(
                f"No pulse streamer with the IP address {self.address}".ljust(65, ".")
                + "[failed]"
            )

    def close(self) -> None:
        """
        There is no function for closing the connection of the device. We have
        tested that running thousands of connections does not slow down the
        execution, we assume we can call pass on the close function.
        """

    def stop(self) -> None:
        """
        Function that stofull_pulse_list the playing sequence and sets all
        the channels to a ZERO state (0V).
        """
        try:
            # define the final state of the Pulsestreamer
            _ = OutputState.ZERO()
            # force the final state.
            self.pulser.forceFinal()
            # print a text if the program has successfully ended
            if not self.pulser.isStreaming():
                print("Stop PulseStreamer: The sequence has been stoped.")
                logger.info(
                    "Pulse Streamer: stopped pulse sequence execution".ljust(65, ".")
                    + "[done]"
                )
            else:
                logger.warning(
                    "Warning full_pulse_listtreamer.stop(): The sequence could\
                            not be stoped".ljust(65, ".")
                    + "[done]"
                )

        except AttributeError:
            logger.exception(
                "Pulse Streamer: error stopping pulse sequence".ljust(65, ".")
                + "[done]"
            )

    def writeDigSeq(self, channel_key: str) -> list[tuple[int, int]]:
        """
        Parameters
        ----------
        channel_key : str
            channel_key of the pulse to be written (LASER, MW or READ)

        Recieves a dictionary of pulses and returns sequence for the
        PulseStreamer of the given channel: LASER, MW or READ.
        """
        self.channel_key = channel_key
        self.seq = []  # sequence of the channel
        pointer_i = 0  # pointer in time
        # Check if the asked channel_key exists in the file
        if self.channel_key not in list(self.pulse_list.keys()):
            logger.error("KeyError: No element named " + str(self.channel_key) + ".")
            raise KeyError

        for i in range(len(self.pulse_list[self.channel_key])):
            # Start and length of the pulse turned into
            # float in case it has str format.
            start_pulse_i = (
                float(
                    self.pulse_list.get(self.channel_key)
                    .get("pulse" + str(i + 1))
                    .get("start")
                )
                * 1e3
            )
            len_pulse_i = (
                float(
                    self.pulse_list.get(self.channel_key)
                    .get("pulse" + str(i + 1))
                    .get("duration")
                )
                * 1e3
            )
            # Check if pulses are well defined
            if pointer_i > start_pulse_i:
                logger.error(
                    "Error: "
                    + self.channel_key
                    + " sequence definition makes no sense, pulses are overlaping!"
                )
                raise PulseSequenceError

            # Check for unsupported analog signals
            freq_pulse_i = (
                self.pulse_list.get(self.channel_key)
                .get("pulse" + str(i + 1))
                .get("frequency")
            )
            if freq_pulse_i != 0:
                logger.warning(
                    "Warning: Frequency different than 0. This programm does not support analog signals. Set frequency to 0."
                )
                raise PulseSequenceError

            ampl_pulse_i = (
                self.pulse_list.get(self.channel_key)
                .get("pulse" + str(i + 1))
                .get("amplitude")
            )
            if ampl_pulse_i != 1 or ampl_pulse_i is None:
                logger.warning(
                    "Warning: Amplitude of the pulse different than 1 (can only be 0 or 1). This programm does not support analog signals. Set amplitude to 1."
                )
                raise PulseSequenceError

            phase_pulse_i = (
                self.pulse_list.get(self.channel_key)
                .get("pulse" + str(i + 1))
                .get("phase")
            )
            if phase_pulse_i != 0:
                logger.warning(
                    "Warning: Phase of the pulse different than 0. This programm does not support analog signals. Set amplitude to 1."
                )
                raise PulseSequenceError

            # Check for non-multples of the sampling time.
            if (start_pulse_i - round(start_pulse_i) != 0) or (
                len_pulse_i - round(len_pulse_i) != 0
            ):
                logger.warning(
                    "Warning: Sampling unit is 1ns. Time values are being rounded."
                )
                # Round the values and turn into integers
                start_pulse_i = int(round(start_pulse_i))
                len_pulse_i = int(round(len_pulse_i))
                # Append a low and a high for each pulse: ___----
                self.seq.append((start_pulse_i - pointer_i, 0))
                self.seq.append((len_pulse_i, 1))
                # Update pointer
                pointer_i = start_pulse_i + len_pulse_i

            else:
                # Turn into integers
                start_pulse_i = int(start_pulse_i)
                len_pulse_i = int(len_pulse_i)
                # Append a low and a high for each pulse: ___----
                self.seq.append((start_pulse_i - pointer_i, 0))
                self.seq.append((len_pulse_i, 1))
                # Update pointer
                pointer_i = start_pulse_i + len_pulse_i

        # Check if the sequence is longer than the defined total time.
        if pointer_i > self.total_duration and self.total_duration_unparsed != "ignore":
            logger.error(
                f"Error: {self.channel_key} duration exceeds the defined total time."
            )
            raise PulseSequenceError
        # Add the final low to make the sequence last its length
        if self.total_duration_unparsed != "ignore":
            self.seq.append((self.total_duration - pointer_i, 0))
        return self.seq

    def plot_sequence(self) -> None:
        """
        From the given file plots the defined sequences
        """
        # check if the sequence has been loaded.
        if self.pulser.isStreaming() is True:
            self.sequence.plot()
        else:
            print(
                "Warning (plot_sequence()): No sequence is streaming in the PulseStreamer."
            )

    def load_sequence(self, ps_yaml_file: str = "sequence_0.yaml") -> None:
        """
        Calls the WriteDigSig() function and loads the corresponding sequences
        to the respective channels.
        """
        try:
            # Selected folder:
            self.yaml_file = set_up.get_seq_dir() / ps_yaml_file
            with open(self.yaml_file, "r", encoding="utf-8") as file:
                full_pulse_list = yaml.load(file, Loader=yaml.FullLoader)
            # PulseStreamer consumes the expanded pulse program directly; it
            # does not implement Tek AWG parent/child sequencer compression.
            _validate_synchroniser_sequence_mode(
                full_pulse_list, "PulseStreamer", ("flat",)
            )
            sequence_order = full_pulse_list["sequencing_order"]
            sequencing_repeats = full_pulse_list["sequencing_repeats"]

            total_duration_unparsed = full_pulse_list["total_duration"]
            self.total_duration_unparsed = total_duration_unparsed
            if total_duration_unparsed == "ignore":
                self.total_duration = np.inf
            if total_duration_unparsed != "ignore":
                total_duration = float(total_duration_unparsed) * 1e3  # convert to ns
                if total_duration - round(total_duration) != 0:
                    logger.warning(
                        "Warning: The total duration is not multiple of the\
                                sampling time and is being rounded!".ljust(65, ".")
                        + "[WARNING]"
                    )
                    self.total_duration = int(round(total_duration))
                else:
                    self.total_duration = int(total_duration)

            # Generate Sequence objects for all pulseseqeunce blocks.
            # These will be sequenced together later.
            sequences_to_write = {}
            for block in set(sequence_order):
                sequences_to_write[block] = Sequence()
                self.pulse_list = full_pulse_list[block]
                self.check_types(self.pulse_list)
                for channel in self.pulse_list:
                    sequences_to_write[block].setDigital(
                        self.channel_mapping[channel], self.writeDigSeq(channel)
                    )
            self.sequence = Sequence()
            for block, repetitions in zip(sequence_order, sequencing_repeats):
                self.sequence += repetitions * sequences_to_write[block]

        except AttributeError:
            logger.exception("pulseseqeunce upload failed")

    def check_types(self, pulse_list) -> None:
        for channel in pulse_list:
            for pulse, pulse_params in pulse_list[channel].items():
                for par, par_value in pulse_params.items():
                    pulse_list[channel][pulse][par] = float(par_value)

    def run(self) -> None:
        """
        Function that triggers the device:
        send trigger + play sequence one time.
        For now pass, write it later.
        """
        try:
            # never runs the seq
            n_runs = 1

            # reset the device - all outputs 0V
            self.pulser.reset()
            self.pulser.constant(OutputState.ZERO())  # all outputs 0V
            final = OutputState.ZERO()

            # Start the sequence after the upload and disable
            # the retrigger-function
            start = TriggerStart.IMMEDIATE
            rearm = TriggerRearm.MANUAL
            self.pulser.setTrigger(start=start, rearm=rearm)

            # upload the sequence and arm the device
            self.pulser.stream(self.sequence, n_runs, final)
            while self.pulser.isStreaming():
                pass
            # check if the sequence has been started correctly.
            logger.info("Pulse Strearmer: sent run signal".ljust(65, ".") + "[done]")

        except AttributeError:
            logger.exception(
                "Pulse Strearmer: problem setting to run".ljust(65, ".") + "[done]"
            )

    def trigger(self) -> None:
        """
        Function that sets the Pulser  to do one loop of the previously-
        defined sequence. It does not ask for a Trigger. It sets the final
        state to the ZERO state. Resets the channels before re-streaming.
        """
        self.pulser.rearm()
        self.pulser.startNow()
        logger.info(
            "Pulse Strearmer: Sent signal to play sequence".ljust(65, ".") + "[done]"
        )
