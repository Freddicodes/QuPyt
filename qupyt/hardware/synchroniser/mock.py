from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ._awg_common import (
    AWGSequenceStep,
    RepeatedChildValidationMixin,
    _validate_awg_limit,
    _validate_synchroniser_sequence_mode,
)
from .base import Synchroniser

logger = logging.getLogger(__name__)


class MockGenerator(RepeatedChildValidationMixin, Synchroniser, synchroniser_types=("MockSynchroniser",)):
    """
    Synchroniser without hardware (registered as ``MockSynchroniser``).

    :meth:`load_sequence` reads and validates the pulse sequence YAML like
    the Tek AWG would (``flat`` and ``repeated_child`` modes, AWG limits),
    so measurement configurations can be dry-run. All other methods only log.

    :param configuration: ``address`` plus the Tek AWG limits
        ``awg_max_sequence_steps`` (default 16384) and
        ``awg_max_step_repeat`` (default 1048576).
    :type configuration: dict | None
    :param channel_mapping: As for the synchroniser that is mocked; not used.
    :type channel_mapping: dict
    """

    def __init__(self, configuration: dict[str, Any] | None, channel_mapping: dict[str, Any]) -> None:
        self.channel_mapping = channel_mapping
        self.device_type: str = "MockSynchroniser"
        self.awg_max_sequence_steps: int = 16_384
        self.awg_max_step_repeat: int = 1_048_576

        Synchroniser.__init__(self, configuration, channel_mapping)
        # Retain TekAWG YAML keys so the mock can dry-run the same config.
        self.attribute_map["awg_max_sequence_steps"] = (
            self._set_awg_max_sequence_steps
        )
        self.attribute_map["awg_max_step_repeat"] = self._set_awg_max_step_repeat
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)
        logger.info("MockSynchroniser instance created".ljust(
            65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchroniser of type MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def open(self) -> None:
        logger.info("Opened MockSynchroniser".ljust(65, ".") + "[done]")

    def load_sequence(self, ps_yaml_file: Path) -> None:
        with open(ps_yaml_file, "r", encoding="utf-8") as file:
            full_pulse_list = yaml.load(file, Loader=yaml.FullLoader)
        total_duration = (
            float(full_pulse_list["total_duration"]) * 1e3
        )  # convert to ns
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
            "Loaded sequence for MockSynchroniser".ljust(
                65, ".") + "[done]"
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

        if len(sequence_order) != len(sequencing_repeats):
            raise ValueError(
                "MockSynchroniser sequence metadata mismatch: "
                f"{len(sequence_order)} sequence entries but "
                f"{len(sequencing_repeats)} repeat counts."
            )

        previous_options = getattr(self, "sequence_options", {})
        self.sequence_options = {
            key: full_pulse_list[key]
            for key in ("awg_sequence_mode", "awg_repeated_child")
            if key in full_pulse_list
        }
        try:
            logical_steps: list[AWGSequenceStep] = []
            for block, repeat in zip(sequence_order, sequencing_repeats, strict=True):
                repeat = int(repeat)
                self._validate_awg_repeat_count(repeat, f"step {len(logical_steps) + 1}")
                logical_steps.append(AWGSequenceStep(str(block), repeat, ()))
            prefix, child, repetitions, suffix = self._validate_repeated_child_region(
                logical_steps
            )
        finally:
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

    def _set_awg_max_sequence_steps(self, max_sequence_steps: int) -> None:
        self.awg_max_sequence_steps = _validate_awg_limit(
            max_sequence_steps,
            "awg_max_sequence_steps",
            "MockSynchroniser",
        )

    def _set_awg_max_step_repeat(self, max_step_repeat: int) -> None:
        self.awg_max_step_repeat = _validate_awg_limit(
            max_step_repeat,
            "awg_max_step_repeat",
            "MockSynchroniser",
        )

    def run(self) -> None:
        logger.info("Sent run to MockSynchroniser".ljust(65, ".") + "[done]")

    def trigger(self) -> None:
        logger.info("Sent trigger from MockSynchroniser".ljust(
            65, ".") + "[done]")

    def stop(self) -> None:
        logger.info("Stopped MockSynchroniser".ljust(65, ".") + "[done]")

    def close(self) -> None:
        logger.info("Closed MockSynchroniser".ljust(65, ".") + "[done]")
