import logging
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import numpy as np
import yaml

from qupyt.mixins import PulseSequenceError
from qupyt.pulse_sequences.sequence_designer import validate_sequence_instructions
from qupyt.utils.errors import cleanup_after_error

from ._awg_common import _validate_synchroniser_sequence_mode
from .base import Synchroniser

logger = logging.getLogger(__name__)


class PStreamer(Synchroniser, synchroniser_types=("SwabInstPS",)):
    """Swabian Instruments Pulse Streamer 8/2 (registered as ``SwabInstPS``).

    Digital channels only; time resolution 1 ns. Only the ``flat`` sequence
    mode is supported.

    Parameters
    ----------
    configuration : dict | None
        ``address`` (IP); without it (or ``"None"``) the
        first Pulse Streamer found on the network is used.
    channel_mapping : dict[str, int]
        Channel name → digital output number.

    Raises
    ------
    ImportError
        if the ``pulsestreamer`` package is missing.
    ConnectionError
        if discovery finds no device.
    """

    def __init__(
        self, configuration: dict[str, Any] | None, channel_mapping: dict[str, int]
    ) -> None:
        try:
            import pulsestreamer  # pylint: disable=import-outside-toplevel
        except ImportError as e:
            raise ImportError(
                "PStreamer needs package 'pulsestreamer'. Install with pip install qupyt[pulsestreamer]"
            ) from e
        self._ps = pulsestreamer
        super().__init__(configuration, channel_mapping)
        self.channel_mapping = channel_mapping
        # Set later (open / load_sequence / writeDigSeq); declared here for readers and mypy.
        self.pulser: Any  # pulsestreamer.PulseStreamer (untyped package)
        self.sequence: Any  # pulsestreamer.Sequence
        self.yaml_file: Path
        self.pulse_list: dict[str, Any]
        self.channel_key: str
        self.seq: list[tuple[int, int]]
        self.total_duration_unparsed: float | str  # number or "ignore"
        self.total_duration: float  # ns; np.inf for "ignore"
        # No configuration or no address: discover the Pulse Streamer on the network.
        configuration = configuration or {}
        self._update_from_configuration(configuration)
        self.initial_configuration_dict = configuration
        if configuration.get("address") in (None, "None"):
            self._find_pulse_streamers()

    def __repr__(self) -> str:
        return f"PStreamer(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchroniser of type PStreamer(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def _find_pulse_streamers(self) -> None:
        devices = self._ps.findPulseStreamers()
        if not devices:
            raise ConnectionError(
                "No Pulse Streamer found on the network. "
                "Set 'address' in the synchroniser configuration."
            )
        logger.info("Detected PulseStreamers: %s", devices)
        self.address = devices[0][0]

    def open(self) -> None:
        """Connect to the Pulse Streamer at ``address``.

        Without an address the device was already discovered on the network in
        ``__init__``.

        Raises
        ------
        ConnectionError
            if the connection (or firmware check) fails.
        """
        try:
            self.pulser = self._ps.PulseStreamer(self.address)
        except AssertionError as e:
            # The pulsestreamer package reports connection and firmware
            # problems with `assert False, msg`.
            raise ConnectionError(
                f"Could not connect to Pulse Streamer at {self.address}: {e}"
            ) from e
        logger.info(
            f"Pulse Streamer connected at {self.address}".ljust(65, ".") + "[done]"
        )

    def close(self) -> None:
        """There is no function for closing the connection of the device.

        We have tested that running thousands of connections does not slow down
        the execution, we assume we can call pass on the close function.
        """

    def stop(self) -> None:
        """Stop the playing sequence.

        All channels go to the final state passed to ``stream()`` in
        :meth:`run` (ZERO, i.e. 0 V).

        Raises
        ------
        RuntimeError
            If the device is still streaming afterwards.
        """
        self.pulser.forceFinal()
        if self.pulser.isStreaming():
            logger.error("Pulse Streamer is still streaming after forceFinal()")
            raise RuntimeError("Pulse Streamer is still streaming after forceFinal()")
        else:
            logger.info(
                "Pulse Streamer: stopped pulse sequence execution".ljust(65, ".")
                + "[done]"
            )

    def writeDigSeq(self, channel_key: str) -> list[tuple[int, int]]:
        """Build the digital pattern of one channel of the current block (``self.pulse_list``, set by :meth:`load_sequence`).

        Times are converted from µs to ns (the Pulse Streamer's resolution)
        and rounded with a warning if needed.

        Parameters
        ----------
        channel_key : str
            Channel name, a key of ``channel_mapping`` (e.g. ``"LASER"``).

        Returns
        -------
        list[tuple[int, int]]
            ``(duration_ns, level)`` pairs, ``level`` 0 or 1.

        Raises
        ------
        KeyError
            if the block has no such channel.
        PulseSequenceError
            for overlapping pulses, analog settings
            (frequency, amplitude, phase) or a sequence longer than ``total_duration``.
        """
        self.channel_key = channel_key
        self.seq = []  # sequence of the channel
        pointer_i = 0  # pointer in time
        # Check if the asked channel_key exists in the file
        if self.channel_key not in list(self.pulse_list.keys()):
            raise KeyError(
                f"No channel named {self.channel_key!r} in the pulse sequence"
            )

        for i in range(len(self.pulse_list[self.channel_key])):
            pulse = f"{self.channel_key} pulse{i + 1}"
            # Start and length of the pulse turned into
            # float in case it has str format.
            start_pulse_i = (
                float(
                    self.pulse_list[self.channel_key]  # key checked above
                    .get("pulse" + str(i + 1))
                    .get("start")
                )
                * 1e3
            )
            len_pulse_i = (
                float(
                    self.pulse_list[self.channel_key]  # key checked above
                    .get("pulse" + str(i + 1))
                    .get("duration")
                )
                * 1e3
            )
            # Check if pulses are well defined
            if pointer_i > start_pulse_i:
                raise PulseSequenceError(
                    f"{pulse}: pulses overlap (starts before the previous pulse ends)"
                )

            # Check for unsupported analog signals
            freq_pulse_i = (
                self.pulse_list[self.channel_key]  # key checked above
                .get("pulse" + str(i + 1))
                .get("frequency")
            )
            if freq_pulse_i != 0:
                raise PulseSequenceError(
                    f"{pulse}: frequency must be 0, the Pulse Streamer "
                    "does not support analog signals"
                )

            ampl_pulse_i = (
                self.pulse_list[self.channel_key]  # key checked above
                .get("pulse" + str(i + 1))
                .get("amplitude")
            )
            if ampl_pulse_i != 1 or ampl_pulse_i is None:
                raise PulseSequenceError(
                    f"{pulse}: amplitude must be 1, the Pulse Streamer "
                    "does not support analog signals"
                )

            phase_pulse_i = (
                self.pulse_list[self.channel_key]  # key checked above
                .get("pulse" + str(i + 1))
                .get("phase")
            )
            if phase_pulse_i != 0:
                raise PulseSequenceError(
                    f"{pulse}: phase must be 0, the Pulse Streamer "
                    "does not support analog signals"
                )

            # Check for non-multples of the sampling time.
            if (start_pulse_i - round(start_pulse_i) != 0) or (
                len_pulse_i - round(len_pulse_i) != 0
            ):
                logger.warning(
                    "Warning: Sampling unit is 1ns. Time values are being rounded."
                )
                # Round the values and turn into integers
                start_pulse_i = round(start_pulse_i)
                len_pulse_i = round(len_pulse_i)
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
            raise PulseSequenceError(
                f"{self.channel_key}: sequence ends at {pointer_i} ns, after "
                f"total_duration {self.total_duration} ns"
            )
        # Add the final low to make the sequence last its length
        if self.total_duration_unparsed != "ignore":
            # int(): total_duration is only a float (np.inf) for "ignore".
            self.seq.append((int(self.total_duration - pointer_i), 0))
        return self.seq

    def plot_sequence(self) -> None:
        """Plot the loaded sequence.

        Only works while the Pulse Streamer is streaming; otherwise a warning
        is logged.
        """
        # check if the sequence has been loaded.
        if self.pulser.isStreaming() is True:
            self.sequence.plot()
        else:
            logger.warning(
                "plot_sequence(): No sequence is streaming in the PulseStreamer."
            )

    def load_sequence(self, ps_yaml_file: Path) -> None:
        """Build the Pulse Streamer sequence from a pulse sequence YAML file.

        One ``Sequence`` per block (:meth:`writeDigSeq` for each channel), then
        the blocks in ``sequencing_order`` with ``sequencing_repeats``. Only
        the ``flat`` sequence mode is supported. Nothing is uploaded here;
        :meth:`run` streams the sequence.

        Parameters
        ----------
        ps_yaml_file : Path
            Path of the pulse sequence YAML file.
        """
        self.yaml_file = ps_yaml_file
        logger.info("Loading Pulse Streamer sequence from %s", self.yaml_file)
        with open(self.yaml_file, "r", encoding="utf-8") as file:
            full_pulse_list = yaml.safe_load(file)
        validate_sequence_instructions(full_pulse_list)
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
                    "The total duration is not a multiple of the sampling "
                    "time and is being rounded".ljust(65, ".") + "[WARNING]"
                )
                self.total_duration = round(total_duration)
            else:
                self.total_duration = int(total_duration)

        # Generate Sequence objects for all pulse sequence blocks.
        # These will be sequenced together later.
        sequences_to_write = {}
        for block in set(sequence_order):
            sequences_to_write[block] = self._ps.Sequence()
            self.pulse_list = full_pulse_list[block]
            self.check_types(self.pulse_list)
            for channel in self.pulse_list:
                sequences_to_write[block].setDigital(
                    self.channel_mapping[channel], self.writeDigSeq(channel)
                )
        self.sequence = self._ps.Sequence()
        for block, repetitions in zip(sequence_order, sequencing_repeats, strict=True):
            self.sequence += repetitions * sequences_to_write[block]

    def check_types(self, pulse_list: dict[str, Any]) -> None:
        """Convert every pulse parameter of the block to ``float`` (in place)."""
        for channel in pulse_list:
            for pulse, pulse_params in pulse_list[channel].items():
                for par, par_value in pulse_params.items():
                    pulse_list[channel][pulse][par] = float(par_value)

    def run(self) -> None:
        """Reset the device (all outputs 0 V), upload the sequence and play it once, starting immediately.

        Blocks until the sequence has finished. The final state is ZERO (0 V).
        :meth:`trigger` plays it again.
        """
        n_runs = 1  # play the sequence once

        # reset the device - all outputs 0V
        self.pulser.reset()
        self.pulser.constant(self._ps.OutputState.ZERO())  # all outputs 0V
        final = self._ps.OutputState.ZERO()

        # Start the sequence after the upload and disable
        # the retrigger-function
        start = self._ps.TriggerStart.IMMEDIATE
        rearm = self._ps.TriggerRearm.MANUAL
        self.pulser.setTrigger(start=start, rearm=rearm)

        # upload the sequence and arm the device
        try:
            self.pulser.stream(self.sequence, n_runs, final)
            # Wait for the sequence to finish, but not forever.
            deadline = monotonic() + self.sequence.getDuration() * 1e-9 + 10
            while self.pulser.isStreaming():
                if monotonic() >= deadline:
                    raise TimeoutError(
                        "Pulse Streamer did not finish within the sequence "
                        "duration plus 10 seconds"
                    )
                sleep(0.01)
        except Exception:
            logger.exception("Pulse Streamer: problem setting to run")
            cleanup_after_error(self.stop, "Pulse Streamer run cleanup")
            raise
        # check if the sequence has been started correctly.
        logger.info("Pulse Streamer: sent run signal".ljust(65, ".") + "[done]")

    def trigger(self) -> None:
        """Play the sequence uploaded by :meth:`run` once more (``rearm`` + ``startNow``), without waiting for a hardware trigger.

        Does not block and does not reset the outputs.
        """
        self.pulser.rearm()
        self.pulser.startNow()
        logger.info(
            "Pulse Streamer: Sent signal to play sequence".ljust(65, ".") + "[done]"
        )
