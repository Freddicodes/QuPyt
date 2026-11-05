from pathlib import Path

import pytest

from qupyt import set_up
from qupyt.set_up import (
    QuPytConfig,
    get_config,
    get_input_dir,
    get_log_dir,
    get_output_dir,
    get_seq_dir,
    get_waiting_room,
    set_config,
)

ENV_VAR = "QUPYT_IN_DIR"
OUT_ENV_VAR = "QUPYT_OUT_DIR"

# The real module defaults, before the autouse fixture replaces them.
REAL_DEFAULT_INPUT_DIR = set_up._DEFAULT_INPUT_DIR


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """
    Keep every test away from the real ~/.qupyt and from each other:
    - no QUPYT_IN_DIR / QUPYT_OUT_DIR from the developer's shell,
    - the default input dir points into tmp_path and the working directory
      (the default output dir) is tmp_path / "default_out",
    - the active config is restored after each test (undoes set_config).
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv(OUT_ENV_VAR, raising=False)
    monkeypatch.setattr(set_up, "_DEFAULT_INPUT_DIR", tmp_path / "default" / ".qupyt")
    (tmp_path / "default_out").mkdir()
    monkeypatch.chdir(tmp_path / "default_out")
    monkeypatch.setattr(set_up, "_config", set_up._config)


# --- choosing the workspace ---------------------------------------------------

def test_real_default_workspace_is_dot_qupyt_in_home():
    assert REAL_DEFAULT_INPUT_DIR == Path.home() / ".qupyt"


def test_default_workspace_is_used_without_arg_or_env(tmp_path):
    assert QuPytConfig().input_dir == tmp_path / "default" / ".qupyt"


def test_env_var_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from_env"))
    assert QuPytConfig().input_dir == tmp_path / "from_env"


def test_argument_beats_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from_env"))
    assert QuPytConfig(tmp_path / "from_arg").input_dir == tmp_path / "from_arg"


def test_empty_env_var_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, "")
    assert QuPytConfig().input_dir == tmp_path / "default" / ".qupyt"


def test_string_argument_becomes_path(tmp_path):
    cfg = QuPytConfig(str(tmp_path / "ws"))
    assert isinstance(cfg.input_dir, Path)
    assert cfg.input_dir == tmp_path / "ws"


def test_tilde_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # expanduser reads HOME on every call
    assert QuPytConfig("~/ws").input_dir == tmp_path / "ws"


def test_subdirectories(tmp_path):
    cfg = QuPytConfig(tmp_path)
    assert cfg.log_dir == tmp_path / "log"
    assert cfg.seq_dir == tmp_path / "sequences"
    assert cfg.waiting_room == tmp_path / "waiting_room"


def test_init_does_not_touch_disk(tmp_path):
    # _config = QuPytConfig() runs at import time; it must not create folders.
    QuPytConfig(tmp_path / "ws")
    assert not (tmp_path / "ws").exists()

def test_relative_in_dir_becomes_absolute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = QuPytConfig("ws")
    assert cfg.input_dir.is_absolute()
    assert cfg.input_dir == tmp_path / "ws"


# --- creating the folders ----------------------------------------------------

def test_create_ws_creates_all_directories(tmp_path):
    cfg = QuPytConfig(tmp_path / "ws", tmp_path / "out")
    cfg.create_ws()
    for directory in (cfg.input_dir, cfg.log_dir, cfg.seq_dir, cfg.waiting_room, cfg.output_dir):
        assert directory.is_dir()


def test_create_ws_creates_missing_parents(tmp_path):
    cfg = QuPytConfig(tmp_path / "a" / "b" / "ws")
    cfg.create_ws()
    assert cfg.waiting_room.is_dir()


def test_create_ws_twice_keeps_existing_files(tmp_path):
    cfg = QuPytConfig(tmp_path / "ws")
    cfg.create_ws()
    marker = cfg.seq_dir / "sequence_0.yaml"
    marker.write_text("keep me")

    cfg.create_ws()

    assert marker.read_text() == "keep me"


def test_create_ws_raises_when_blocked_by_file(tmp_path):
    # A file where the workspace should be; more portable than permissions.
    blocker = tmp_path / "ws"
    blocker.write_text("not a directory")
    with pytest.raises(OSError):
        QuPytConfig(blocker).create_ws()


# --- active config and helpers ----------------------------------------------

def test_set_config_changes_all_helpers(tmp_path):
    cfg = QuPytConfig(tmp_path, tmp_path / "out")
    set_config(cfg)
    assert get_config() is cfg
    assert get_input_dir() == tmp_path
    assert get_log_dir() == tmp_path / "log"
    assert get_seq_dir() == tmp_path / "sequences"
    assert get_waiting_room() == tmp_path / "waiting_room"
    assert get_output_dir() == tmp_path / "out"


def test_helpers_read_the_active_config_on_every_call(tmp_path):
    set_config(QuPytConfig(tmp_path / "first"))
    assert get_seq_dir() == tmp_path / "first" / "sequences"

    set_config(QuPytConfig(tmp_path / "second"))
    assert get_seq_dir() == tmp_path / "second" / "sequences"


# --- output directory ---------------------------------------------------------

def test_default_output_dir_is_used_without_arg_or_env(tmp_path):
    assert QuPytConfig().output_dir == tmp_path / "default_out"


def test_output_dir_env_var_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv(OUT_ENV_VAR, str(tmp_path / "out_env"))
    assert QuPytConfig().output_dir == tmp_path / "out_env"


def test_output_dir_argument_beats_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(OUT_ENV_VAR, str(tmp_path / "out_env"))
    cfg = QuPytConfig(output_dir=tmp_path / "out_arg")
    assert cfg.output_dir == tmp_path / "out_arg"


def test_in_dir_env_var_does_not_change_output_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "in_env"))
    assert QuPytConfig().output_dir == tmp_path / "default_out"
