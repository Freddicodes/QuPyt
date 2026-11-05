import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class AWGSequenceStep:
    """One expanded logical AWG sequencer step.

    Repeated-child compression must compare all hardware-visible state, not just
    waveform names; repeat counts and flag states are part of the timing contract.
    """

    waveform: str
    repeat: int
    flags: tuple[str, ...]

class RepeatedChildValidationMixin:
    """
    Validation of the Tek AWG ``repeated_child`` sequence layout, shared by
    :class:`AWGenerator` and the mock synchroniser (which dry-runs it).

    The using class has to set the attributes declared below.
    """

    device_type: str
    awg_max_sequence_steps: int
    awg_max_step_repeat: int
    sequence_options: dict[str, Any]

    def _validate_awg_repeat_count(self, repeat: int, context: str) -> None:
        if repeat <= 0:
            raise ValueError(f"{self.device_type} {context} repeat count must be positive.")
        if repeat > self.awg_max_step_repeat:
            raise ValueError(
                f"{self.device_type} {context} repeat count {repeat} exceeds the "
                f"configured AWG limit of {self.awg_max_step_repeat}."
            )

    def _validate_repeated_child_region(
        self, steps: list[AWGSequenceStep]
    ) -> tuple[list[AWGSequenceStep], list[AWGSequenceStep], int, list[AWGSequenceStep]]:
        config = self.sequence_options.get("awg_repeated_child", {})
        if not isinstance(config, dict):
            # ValueError, not TypeError: like every other check here, this is
            # an invalid value in the YAML configuration.
            raise ValueError(  # noqa: TRY004
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
        if child_steps > self.awg_max_sequence_steps:
            raise ValueError(
                f"Repeated child sequence has {child_steps} steps, exceeding "
                f"the configured AWG limit of {self.awg_max_sequence_steps}."
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
                    "awg_repeated_child repetitions must be > 0, got "
                    f"{repetitions}."
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
                        f"{repetition_index + 1}. Waveform names, repeat counts, "
                        "and flags must match exactly for repeated_child mode."
                    )
        self._validate_awg_repeat_count(repetitions, "child-sequence parent")
        suffix_start = prefix_steps + repetitions * child_steps
        suffix = steps[suffix_start:]
        compact_body_steps = prefix_steps + 1 + len(suffix)
        if compact_body_steps > self.awg_max_sequence_steps:
            raise ValueError(
                f"Compact AWG parent/body sequence has {compact_body_steps} steps, "
                f"exceeding the configured AWG limit of {self.awg_max_sequence_steps}."
            )
        return steps[:prefix_steps], child, repetitions, suffix

    def _infer_child_repetitions(
        self, steps: list[AWGSequenceStep], prefix_steps: int, child: list[AWGSequenceStep]
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


def _validate_synchroniser_sequence_mode(
    sequence_instructions: dict[str, Any],
    device_type: str,
    supported_modes: tuple[str, ...],
) -> str:
    """Reject sequence upload modes unsupported by the selected synchroniser.

    awg_sequence_mode is not part of the logical pulse program. It only
    describes how TekAWG should upload that pulse program. Non-AWG devices
    reject unsupported modes explicitly so a Tek-only YAML option is not
    silently ignored.
    """

    mode = str(sequence_instructions.get("awg_sequence_mode", "flat"))
    if mode not in supported_modes:
        raise ValueError(
            f"awg_sequence_mode: {mode} is not supported by {device_type}. "
            f"Supported modes for {device_type}: {', '.join(supported_modes)}."
        )
    logger.info("%s accepted awg_sequence_mode=%s", device_type, mode)
    return mode


def _validate_awg_limit(value: Any, key: str, device_type: str) -> int:
    """Validate an AWG hardware limit from synchroniser.config.

    Defaults are assigned in the synchroniser __init__; this helper is only
    called when the YAML provides an override for another AWG model.
    """

    try:
        limit = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"{key} must be an integer AWG limit for {device_type}, got {value!r}."
        ) from e
    if limit <= 0:
        raise ValueError(
            f"{key} must be a positive AWG limit for {device_type}, got {limit}."
        )
    logger.info("%s configured %s=%s", device_type, key, limit)
    return limit
