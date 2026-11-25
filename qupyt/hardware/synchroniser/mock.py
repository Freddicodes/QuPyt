import logging
from typing import Any

import yaml

from qupyt.config import get_settings
from qupyt.utils.logging import DONE

from .awgenerator import AWGenerator
from .base import (
    Synchroniser,
    _validate_awg_limit,
    _validate_synchroniser_sequence_mode,
)

logger = logging.getLogger(__name__)


class MockGenerator(Synchroniser, synchroniser_types=("MockSynchroniser",)):
    def __init__(self, configuration: dict[str, Any], channel_mapping: dict[str, Any]):
        self.channel_mapping = channel_mapping
        self.device_type: str = "MockGenerator"
        self.max_sequence_steps: int = 16_384
        self.max_step_repeat: int = 1_048_576

        Synchroniser.__init__(self, configuration, channel_mapping)
        # Retain TekAWG YAML keys so the mock can dry-run the same config.
        self.attribute_map["awg_max_sequence_steps"] = self._set_max_sequence_steps
        self.attribute_map["awg_max_step_repeat"] = self._set_max_step_repeat
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)
        logger.info("MockSynchroniser instance created", extra=DONE)

    def __repr__(self) -> str:
        return f"MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def __str__(self) -> str:
        return f"Synchronizer of type MockGenerator(configuration: {self.initial_configuration_dict}, channel_mapping: {self.channel_mapping})"

    def open(self) -> None:
        logger.info("Opened MockSynchroniser", extra=DONE)

    def load_sequence(self, ps_yaml_file: str = "sequence_0.yaml") -> None:
        try:
            self.yaml_file = get_settings().sequence_dir / ps_yaml_file
            with open(self.yaml_file, "r", encoding="utf-8") as file:
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
                self.total_duration = int(round(total_duration))
            else:
                self.total_duration = int(total_duration)

            logger.info("Loaded sequence for MockSynchroniser", extra=DONE)

        except AttributeError:
            logger.exception(
                "Caught attribute error when writing loading from yaml pulse sequence"
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
            logical_steps = []
            for block, repeat in zip(sequence_order, sequencing_repeats):
                repeat = int(repeat)
                self._validate_awg_repeat_count(
                    repeat, f"step {len(logical_steps) + 1}"
                )
                logical_steps.append(
                    AWGenerator.AWGSequenceStep(str(block), repeat, ())
                )
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

    def _validate_awg_repeat_count(self, repeat: int, context: str) -> None:
        if repeat <= 0:
            raise ValueError(
                f"MockSynchroniser AWG {context} repeat count must be positive."
            )
        if repeat > self.max_step_repeat:
            raise ValueError(
                f"MockSynchroniser AWG {context} repeat count {repeat} exceeds "
                f"the configured AWG limit of {self.max_step_repeat}."
            )

    def _set_max_sequence_steps(self, max_sequence_steps: int) -> None:
        self.max_sequence_steps = _validate_awg_limit(
            max_sequence_steps,
            "awg_max_sequence_steps",
            "MockSynchroniser",
        )

    def _set_max_step_repeat(self, max_step_repeat: int) -> None:
        self.max_step_repeat = _validate_awg_limit(
            max_step_repeat,
            "awg_max_step_repeat",
            "MockSynchroniser",
        )

    def _validate_repeated_child_region(
        self, steps: list[AWGenerator.AWGSequenceStep]
    ) -> tuple[
        list[AWGenerator.AWGSequenceStep],
        list[AWGenerator.AWGSequenceStep],
        int,
        list[AWGenerator.AWGSequenceStep],
    ]:
        config = self.sequence_options.get("awg_repeated_child", {})
        if not isinstance(config, dict):
            raise ValueError(
                "awg_repeated_child must be a mapping with prefix_steps, "
                "child_steps, and optionally repetitions."
            )
        try:
            prefix_steps = int(config.get("prefix_steps", 0))
            child_steps = int(config["child_steps"])
        except KeyError as exc:
            raise ValueError(
                "awg_repeated_child requires child_steps for repeated_child mode."
            ) from exc

        repetitions_config = config.get("repetitions")
        if prefix_steps < 0:
            raise ValueError(
                f"awg_repeated_child prefix_steps must be >= 0, got {prefix_steps}."
            )
        if child_steps <= 0:
            raise ValueError(
                f"awg_repeated_child child_steps must be > 0, got {child_steps}."
            )
        if child_steps > self.max_sequence_steps:
            raise ValueError(
                f"Repeated child sequence has {child_steps} steps, exceeding "
                f"the configured AWG limit of {self.max_sequence_steps}."
            )
        if prefix_steps + child_steps > len(steps):
            raise ValueError(
                "awg_repeated_child prefix_steps + child_steps exceeds the "
                f"logical sequence length: {prefix_steps} + {child_steps} > "
                f"{len(steps)}."
            )

        child = steps[prefix_steps : prefix_steps + child_steps]
        if repetitions_config is None:
            repetitions = self._infer_child_repetitions(steps, prefix_steps, child)
        else:
            repetitions = int(repetitions_config)
            if repetitions <= 0:
                raise ValueError(
                    f"awg_repeated_child repetitions must be > 0, got {repetitions}."
                )
            expected_end = prefix_steps + repetitions * child_steps
            if expected_end > len(steps):
                available_steps = len(steps) - prefix_steps
                max_complete_repetitions = available_steps // child_steps
                raise ValueError(
                    "awg_repeated_child does not match the expanded logical "
                    "sequence. The configured repeated region would end after "
                    f"{prefix_steps} + {repetitions} * {child_steps} = "
                    f"{expected_end} steps, but the generated sequence has only "
                    f"{len(steps)} steps. After the {prefix_steps} prefix steps, "
                    f"only {available_steps} steps remain, which can contain at "
                    f"most {max_complete_repetitions} complete child repetitions "
                    f"of {child_steps} steps. Check that n_meas/repetitions and "
                    "len(cycle_order)/child_steps are calculated from the same "
                    "sequence that is written to sequencing_order."
                )
            for repetition_index in range(repetitions):
                start = prefix_steps + repetition_index * child_steps
                current_child = steps[start : start + child_steps]
                if current_child != child:
                    raise ValueError(
                        "Repeated child block mismatch at repetition "
                        f"{repetition_index + 1}. Sequence names and repeat "
                        "counts must match exactly for repeated_child mode."
                    )

        self._validate_awg_repeat_count(repetitions, "child-sequence parent")
        suffix_start = prefix_steps + repetitions * child_steps
        suffix = steps[suffix_start:]
        compact_body_steps = prefix_steps + 1 + len(suffix)
        if compact_body_steps > self.max_sequence_steps:
            raise ValueError(
                f"Compact AWG parent/body sequence has {compact_body_steps} steps, "
                f"exceeding the configured AWG limit of {self.max_sequence_steps}."
            )
        return steps[:prefix_steps], child, repetitions, suffix

    def _infer_child_repetitions(
        self,
        steps: list[AWGenerator.AWGSequenceStep],
        prefix_steps: int,
        child: list[AWGenerator.AWGSequenceStep],
    ) -> int:
        child_steps = len(child)
        repetitions = 0
        start = prefix_steps
        while steps[start : start + child_steps] == child:
            repetitions += 1
            start += child_steps
        if repetitions < 2:
            raise ValueError(
                "Could not infer awg_repeated_child repetitions: at least two "
                "identical child occurrences are required."
            )
        return repetitions

    def run(self) -> None:
        logger.info("Sent run to MockSynchroniser", extra=DONE)

    def trigger(self) -> None:
        logger.info("Sent trigger from MockSynchroniser", extra=DONE)

    def stop(self) -> None:
        logger.info("Stopped MockSynchroniser", extra=DONE)

    def close(self) -> None:
        logger.info("Closed MockSynchroniser", extra=DONE)
