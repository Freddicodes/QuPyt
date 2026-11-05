from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from qupyt.pulse_sequences.sequence_designer import validate_sequence_instructions

from ._awg_common import (
    DEFAULT_MAX_SEQUENCE_STEPS,
    DEFAULT_MAX_STEP_REPEAT,
    AWGSequenceStep,
    _validate_synchroniser_sequence_mode,
    build_logical_awg_steps,
    get_awg_sequence_options,
    infer_child_repetitions,
    validate_awg_limit,
    validate_awg_repeat_count,
    validate_repeated_child_region,
)
from .base import Synchroniser

logger = logging.getLogger(__name__)


class MockGenerator(Synchroniser, synchroniser_types=("MockSynchroniser",)):
    """Synchroniser without hardware (registered as ``MockSynchroniser``).

    :meth:`load_sequence` reads and validates the pulse sequence YAML like
    the Tek AWG would (``flat`` and ``repeated_child`` modes, AWG limits),
    so measurement configurations can be dry-run. All other methods only log.

    Parameters
    ----------
    configuration : dict | None
        ``address`` plus the Tek AWG limits
        ``awg_max_sequence_steps`` (default 16384) and
        ``awg_max_step_repeat`` (default 1048576).
    channel_mapping : dict
        As for the synchroniser that is mocked; not used.
    """

    def __init__(
        self, configuration: dict[str, Any] | None, channel_mapping: dict[str, Any]
    ) -> None:
        self.channel_mapping = channel_mapping
        self.device_type: str = "MockSynchroniser"
        self.awg_max_sequence_steps: int = DEFAULT_MAX_SEQUENCE_STEPS
        self.awg_max_step_repeat: int = DEFAULT_MAX_STEP_REPEAT
        self.sequence_options: dict[str, Any] = {}

        Synchroniser.__init__(self, configuration, channel_mapping)
        # Retain TekAWG YAML keys so the mock can dry-run the same config.
        self.attribute_map["awg_max_sequence_steps"] = self._set_awg_max_sequence_steps
        self.attribute_map["awg_max_step_repeat"] = self._set_awg_max_step_repeat
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)
        logger.info("MockSynchroniser instance created".ljust(65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchroniser of type MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def open(self) -> None:
        logger.info("Opened MockSynchroniser".ljust(65, ".") + "[done]")

    def load_sequence(self, ps_yaml_file: Path) -> None:
        with open(ps_yaml_file, "r", encoding="utf-8") as file:
            full_pulse_list = yaml.safe_load(file)
        validate_sequence_instructions(full_pulse_list)
        total_duration = float(full_pulse_list["total_duration"]) * 1e3  # convert to ns
        sequence_order = full_pulse_list["sequencing_order"]
        sequencing_repeats = full_pulse_list["sequencing_repeats"]
        # Mock accepts repeated_child as a dry run of the Tek AWG upload
        # strategy. It validates the metadata but still has no hardware
        # sequencer to program.
        _validate_synchroniser_sequence_mode(
            full_pulse_list, "MockSynchroniser", ("flat", "repeated_child")
        )
        self._validate_awg_sequence_options(
            full_pulse_list, sequence_order, sequencing_repeats
        )
        if total_duration - round(total_duration) != 0:
            logger.warning(
                "Warning: The total duration is not multiple of the sampling time and is being rounded!"
            )
            self.total_duration = round(total_duration)
        else:
            self.total_duration = int(total_duration)

        logger.info(
            f"Loaded sequence for MockSynchroniser from {ps_yaml_file}".ljust(65, ".")
            + "[done]"
        )

    def _validate_awg_sequence_options(
        self,
        full_pulse_list: dict[str, Any],
        sequence_order: list[str],
        sequencing_repeats: list[int],
    ) -> None:
        """Dry-run TekAWG sequence-mode metadata for mock measurements.

        The mock does not send SCPI commands or compress waveforms. It checks
        that the expanded logical sequence can be represented by the configured
        repeated_child parent/child layout, so mock measurements catch the same
        configuration mistakes before the YAML is sent to a real AWG.
        """
        mode = str(full_pulse_list.get("awg_sequence_mode", "flat"))
        if mode == "flat":
            logger.info("MockSynchroniser AWG sequence mode: flat")
            return
        if mode != "repeated_child":
            raise ValueError(
                f"Unsupported awg_sequence_mode for MockSynchroniser: {mode!r}. "
                "Supported values are 'flat' and 'repeated_child'."
            )

        sequence_options = get_awg_sequence_options(full_pulse_list)
        logical_steps = build_logical_awg_steps(
            sequence_order,
            sequencing_repeats,
            AWGSequenceStep,
            max_step_repeat=self.awg_max_step_repeat,
            device_type="MockSynchroniser AWG",
        )
        previous_options = self.sequence_options
        self.sequence_options = sequence_options
        try:
            prefix, child, repetitions, suffix = self._validate_repeated_child_region(
                logical_steps
            )
        finally:
            # The mock only dry-runs the upload; keep its state unchanged.
            self.sequence_options = previous_options

        logger.info(
            "MockSynchroniser AWG sequence mode: repeated_child; "
            "expanded_logical_steps=%s; prefix_steps=%s; child_steps=%s; "
            "child_repetitions=%s; suffix_steps=%s",
            len(logical_steps),
            len(prefix),
            len(child),
            repetitions,
            len(suffix),
        )

    # Thin wrappers around the shared functions with this synchroniser's limits.
    def _validate_repeated_child_region(
        self, steps: list[AWGSequenceStep]
    ) -> tuple[
        list[AWGSequenceStep], list[AWGSequenceStep], int, list[AWGSequenceStep]
    ]:
        return validate_repeated_child_region(
            steps,
            self.sequence_options.get("awg_repeated_child", {}),
            max_sequence_steps=self.awg_max_sequence_steps,
            max_step_repeat=self.awg_max_step_repeat,
            device_type="MockSynchroniser AWG",
        )

    def _validate_awg_repeat_count(self, repeat: int, context: str) -> None:
        validate_awg_repeat_count(
            repeat, self.awg_max_step_repeat, context, "MockSynchroniser AWG"
        )

    def _infer_child_repetitions(
        self,
        steps: list[AWGSequenceStep],
        prefix_steps: int,
        child: list[AWGSequenceStep],
    ) -> int:
        return infer_child_repetitions(steps, prefix_steps, child)

    def _set_awg_max_sequence_steps(self, max_sequence_steps: int) -> None:
        self.awg_max_sequence_steps = validate_awg_limit(
            max_sequence_steps, "awg_max_sequence_steps", "MockSynchroniser"
        )

    def _set_awg_max_step_repeat(self, max_step_repeat: int) -> None:
        self.awg_max_step_repeat = validate_awg_limit(
            max_step_repeat, "awg_max_step_repeat", "MockSynchroniser"
        )

    def run(self) -> None:
        logger.info("Sent run to MockSynchroniser".ljust(65, ".") + "[done]")

    def trigger(self) -> None:
        logger.info("Sent trigger from MockSynchroniser".ljust(65, ".") + "[done]")

    def stop(self) -> None:
        logger.info("Stopped MockSynchroniser".ljust(65, ".") + "[done]")

    def close(self) -> None:
        logger.info("Closed MockSynchroniser".ljust(65, ".") + "[done]")
