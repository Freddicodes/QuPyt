"""yaml_sequence and pulse_sequence_handler: writing sequences and running user scripts."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from qupyt import set_up
from qupyt.pulse_sequences.pulse_sequence_handler import (
    update_params_dict,
    write_user_ps,
)
from qupyt.pulse_sequences.yaml_sequence import (
    ArbitrarySequenceWriter,
    ComplexSequence,
    YamlSequence,
)
from qupyt.set_up import QuPytConfig, set_config


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setattr(set_up, "_config", set_up._config)  # restored afterwards
    config = QuPytConfig(tmp_path / "in", tmp_path / "out")
    config.create_ws()
    set_config(config)
    return config


def written(workspace: Any, number: Any = 0) -> Any:
    path = workspace.seq_dir / f"sequence_{number}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- YamlSequence -------------------------------------------------------------


def test_write_produces_the_synchroniser_layout(workspace: Any) -> None:
    seq = YamlSequence(duration=10)
    seq.add_pulse("LASER", 0, 3)
    seq.add_pulse("LASER", 5, 1, sequence_blocks=["block_0", "block_1"])
    seq.add_pulse("MW", 4, 0.5, frequency=2e6, phase=np.pi)
    seq.sequencing_order = ["block_0", "block_1"]
    seq.sequencing_repeats = [1, 3]
    seq.write(2)

    content = written(workspace, 2)
    assert content["total_duration"] == 10
    assert content["sequencing_order"] == ["block_0", "block_1"]
    assert content["sequencing_repeats"] == [1, 3]
    assert list(content["block_0"]["LASER"]) == ["pulse1", "pulse2"]
    assert content["block_1"]["LASER"]["pulse1"]["start"] == 5
    assert content["block_0"]["MW"]["pulse1"] == {
        "start": 4,
        "duration": 0.5,
        "amplitude": 1.0,
        "frequency": 2e6,
        "phase": pytest.approx(np.pi),
    }


def test_write_defaults_to_sequence_0(workspace: Any) -> None:
    seq = YamlSequence(duration=1)
    seq.sequencing_order, seq.sequencing_repeats = ["block_0"], [1]
    seq.write()
    assert (workspace.seq_dir / "sequence_0.yaml").exists()


def test_write_without_sequencing_order_raises(workspace: Any) -> None:
    with pytest.raises(AttributeError):
        YamlSequence(duration=1).write()


# --- ComplexSequence.gen_phases ------------------------------------------------


def make_complex() -> Any:
    return ComplexSequence(
        YamlSequence(10), "MW", tau=1, pi_half_pulse_dur=0.1, pi_pulse_dur=0.2
    )


def test_xy8_phases() -> None:
    seq = make_complex()
    seq.gen_phases("XY8", n=2, readout_phase=0)
    block = [0, np.pi / 2, 0, np.pi / 2, np.pi / 2, 0, np.pi / 2, 0]
    np.testing.assert_allclose(seq.phases, [0] + block * 2 + [0])


@pytest.mark.parametrize("seq_type", ["XY4", "XY16", "UD4", "UD6"])
def test_supported_lengths(seq_type: Any) -> None:
    seq = make_complex()
    seq.gen_phases(seq_type, n=1)
    assert len(seq.phases) == int(seq_type[2:]) + 2


@pytest.mark.parametrize("seq_type", ["XY6", "XY12", "UD2", "UD3", "KDD4", "XY"])
def test_invalid_sequence_types_raise(seq_type: Any) -> None:
    with pytest.raises(ValueError):
        make_complex().gen_phases(seq_type)


def test_write_sequence_spaces_pi_pulses_by_two_tau() -> None:
    seq = make_complex()
    seq.gen_phases("XY4", n=1)
    seq.write_sequence(start=0)
    pulses = seq.sequence.pulse_sequence["block_0"]["MW"]
    starts = [pulses[f"pulse{k}"]["start"] for k in range(1, len(pulses) + 1)]
    # pi/2 at 0, first pi at tau (ts_start=1), then every 2 tau
    np.testing.assert_allclose(starts[:5], [0, 1, 3, 5, 7])


# --- ArbitrarySequenceWriter ---------------------------------------------------


def test_cpmg_prepare_and_write() -> None:
    writer = ArbitrarySequenceWriter(
        "MW", N=3, pi=0.2, pi_half=0.1, tau=1, res_mix_freq=0
    )
    length = writer.prepare_sequence("CPMG")
    assert length == pytest.approx(3 * 2 + 0.1)
    seq = YamlSequence(10)
    end = writer.write_sequence(seq, start=0)
    assert end == pytest.approx(6)
    starts = [p["start"] for p in seq.pulse_sequence["block_0"]["MW"].values()]
    np.testing.assert_allclose(starts, [1, 3, 5])


def test_unsupported_arbitrary_sequence_raises() -> None:
    writer = ArbitrarySequenceWriter(
        "MW", N=1, pi=0.2, pi_half=0.1, tau=1, res_mix_freq=0
    )
    with pytest.raises(ValueError, match="not supported"):
        writer.prepare_sequence("XY16")


# --- pulse_sequence_handler ------------------------------------------------------


def test_update_params_dict_merges_nested_dicts() -> None:
    params = {"a": 1, "data": {"averaging_mode": "sum", "dynamic_steps": 1}}
    result = update_params_dict(params, {"data": {"dynamic_steps": 5}, "b": 2})
    assert result is params
    assert params == {
        "a": 1,
        "b": 2,
        "data": {"averaging_mode": "sum", "dynamic_steps": 5},
    }


def test_update_params_dict_with_none_changes_nothing() -> None:
    params = {"a": 1}
    assert update_params_dict(params, None) == {"a": 1}


def test_update_params_dict_creates_missing_nested_dicts() -> None:
    params: dict[str, object] = {}
    update_params_dict(params, {"sensor": {"config": {"exposure_time": 5}}})
    assert params == {"sensor": {"config": {"exposure_time": 5}}}


def test_write_user_ps_runs_generate_sequence(tmp_path: Path) -> None:
    script = tmp_path / "my_sequence.py"
    script.write_text(
        "def generate_sequence(params):\n" "    return {'averages': params['n'] * 2}\n",
        encoding="utf-8",
    )
    assert write_user_ps(script, {"n": 4}) == {"averages": 8}


def test_write_user_ps_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_user_ps(tmp_path / "missing.py", {})
