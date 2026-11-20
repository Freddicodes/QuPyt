import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from qupyt import config
from qupyt.config import get_settings, load_settings, make_userdirs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No user config, env file or QUPYT_* variables; fresh active settings."""
    for key in list(os.environ):
        if key.startswith("QUPYT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("QUPYT_CONFIG_FILE", str(tmp_path / "config.toml"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_settings", None)
    return tmp_path


def test_defaults():
    settings = load_settings([])
    assert settings.home_dir == config.DEFAULT_HOME_DIR
    assert settings.log_dir == config.DEFAULT_HOME_DIR / "log"
    assert settings.log_level == "INFO"
    assert settings.log_to_console is False
    assert settings.spinapi_library is None


def test_precedence(isolated, monkeypatch):
    (isolated / "config.toml").write_text(
        'log_level = "DEBUG"\nhome_dir = "/toml"\nlog_dir = "/toml-log"\nspinapi_library = "/toml.so"\n'
    )
    (isolated / ".env").write_text("QUPYT_LOG_LEVEL=WARNING\nQUPYT_HOME_DIR=/dotenv\nQUPYT_LOG_DIR=/dotenv-log\n")
    monkeypatch.setenv("QUPYT_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("QUPYT_HOME_DIR", "/env")

    settings = load_settings(["--log-level", "INFO"])

    assert settings.log_level == "INFO"  # command line
    assert settings.home_dir == Path("/env")  # environment
    assert settings.log_dir == Path("/dotenv-log")  # env file
    assert settings.spinapi_library == Path("/toml.so")  # config file


def test_directories_follow_home_dir(isolated):
    (isolated / "config.toml").write_text('home_dir = "/data/qupyt"\nlog_dir = "/var/log/qupyt"\n')
    settings = load_settings([])
    assert settings.sequence_dir == Path("/data/qupyt/sequences")
    assert settings.waiting_room_dir == Path("/data/qupyt/waiting_room")
    assert settings.log_dir == Path("/var/log/qupyt")


def test_file_locations_from_command_line(isolated):
    (isolated / "other.toml").write_text('log_level = "DEBUG"\n')
    (isolated / "other.env").write_text("QUPYT_LOG_TO_CONSOLE=true\n")
    settings = load_settings(["--config-file", "other.toml", "--env-file", "other.env"])
    assert settings.log_level == "DEBUG"
    assert settings.log_to_console is True


@pytest.mark.parametrize("flag", ["--verbose", "--log-to-console"])
def test_console_flag(flag):
    assert load_settings([flag]).log_to_console is True


def test_invalid_log_level():
    with pytest.raises(ValidationError):
        load_settings(["--log-level", "LOUD"])


def test_unknown_config_key_is_rejected(isolated):
    (isolated / "config.toml").write_text('log_levle = "DEBUG"\n')
    with pytest.raises(ValidationError):
        load_settings([])


def test_get_settings_ignores_command_line(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pytest", "--log-level", "DEBUG"])
    assert get_settings().log_level == "INFO"


def test_make_userdirs_uses_active_settings(isolated):
    load_settings(["--home-dir", str(isolated / "home")])
    assert get_settings().sequence_dir == isolated / "home" / "sequences"
    make_userdirs()
    assert (isolated / "home" / "waiting_room").is_dir()
    assert (isolated / "home" / "log").is_dir()


def test_home_shortcut_is_expanded(isolated):
    (isolated / "config.toml").write_text('home_dir = "~/qupyt-data"\n')
    settings = load_settings([])
    assert settings.home_dir == Path.home() / "qupyt-data"
    assert settings.log_dir == Path.home() / "qupyt-data" / "log"


def test_example_config_is_valid(isolated):
    """Every setting in config.example.toml, uncommented, loads."""
    example = Path(__file__).parents[1] / "config.example.toml"
    lines = [
        line.removeprefix("# ")
        for line in example.read_text().splitlines()
        if line.startswith("# ") and " = " in line and not line.startswith("#   ")
    ]
    (isolated / "config.toml").write_text("\n".join(lines) + "\n")
    settings = load_settings([])
    defaults = load_settings(["--config-file", "none.toml"])
    differs = {"config_file", "spinapi_library"}
    assert settings.model_dump(exclude=differs) == defaults.model_dump(exclude=differs)
    assert settings.spinapi_library == Path("/opt/spincore/lib/libspinapi.so")
