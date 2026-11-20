"""
Application settings of QuPyt.

Settings are read from these sources, highest priority first:

1. Command line arguments, e.g. ``qupyt --log-level DEBUG``.
2. Environment variables with the ``QUPYT_`` prefix, e.g. ``QUPYT_LOG_LEVEL=DEBUG``.
3. An env file (default ``.env`` in the working directory).
4. A TOML config file (default ``~/.qupyt/config.toml``).
5. The defaults below.

The env file and config file locations are themselves settings
(``--env-file`` / ``QUPYT_ENV_FILE`` and ``--config-file`` /
``QUPYT_CONFIG_FILE``); they are only read from the command line and the
environment.

Measurement instructions (the YAML files in the waiting room) are not part of
these settings.
"""

import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    CliSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_HOME_DIR = Path.home() / ".qupyt"


class Settings(BaseSettings):
    """Start QuPyt and run the measurement files placed in the waiting room."""

    model_config = SettingsConfigDict(
        env_prefix="QUPYT_",
        cli_prog_name="qupyt",
        cli_kebab_case=True,
        cli_implicit_flags=True,
        # --verbose is the flag of earlier QuPyt versions.
        cli_shortcuts={"log-to-console": "verbose"},
    )

    config_file: Path = Field(
        DEFAULT_HOME_DIR / "config.toml", description="TOML config file."
    )
    env_file: Path = Field(Path(".env"), description="Env file with QUPYT_* variables.")
    home_dir: Path = Field(DEFAULT_HOME_DIR, description="QuPyt user directory.")
    log_dir: Path = Field(DEFAULT_HOME_DIR / "log", description="Default: <home-dir>/log.")
    sequence_dir: Path = Field(
        DEFAULT_HOME_DIR / "sequences", description="Default: <home-dir>/sequences."
    )
    waiting_room_dir: Path = Field(
        DEFAULT_HOME_DIR / "waiting_room", description="Default: <home-dir>/waiting_room."
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_to_console: bool = Field(
        False, description="Also print log messages to the console."
    )
    spinapi_library: Path | None = Field(
        None, description="Path of the SpinCore SpinAPI library (PulseBlaster)."
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_directories(cls, data: Any) -> Any:
        """Place directories that are not set explicitly inside ``home_dir``."""
        if isinstance(data, dict) and "home_dir" in data:
            home_dir = Path(data["home_dir"])
            data.setdefault("log_dir", home_dir / "log")
            data.setdefault("sequence_dir", home_dir / "sequences")
            data.setdefault("waiting_room_dir", home_dir / "waiting_room")
        return data

    @field_validator(
        "config_file", "env_file", "home_dir", "log_dir", "sequence_dir",
        "waiting_room_dir", "spinapi_library",
    )
    @classmethod
    def _expand_user(cls, path: Path | None) -> Path | None:
        """Expand ``~``, which the shell does not do for files and quoted values."""
        return None if path is None else path.expanduser()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The command line is passed in as init settings (see load_settings).
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
        )


_settings: Settings | None = None


def load_settings(argv: list[str] | None = None) -> Settings:
    """Read the settings from all sources and make them the active settings.

    Parameters
    ----------
    argv : list[str] | None
        Command line arguments without the program name. ``None`` reads
        ``sys.argv[1:]``.

    Returns
    -------
    Settings
        The loaded settings, also returned by :func:`get_settings` from now on.
    """
    global _settings
    if argv is None:
        argv = sys.argv[1:]

    # First pass without files: find out which env file and config file to read.
    locations = Settings(_cli_settings_source=CliSettingsSource(Settings, cli_parse_args=argv))

    class _Settings(Settings):
        model_config = SettingsConfigDict(
            env_file=locations.env_file, toml_file=locations.config_file
        )

    _settings = _Settings(
        _cli_settings_source=CliSettingsSource(_Settings, cli_parse_args=argv)
    )
    return _settings


def get_settings() -> Settings:
    """Return the active settings.

    If :func:`load_settings` has not been called yet, the settings are loaded
    without command line arguments.
    """
    if _settings is None:
        return load_settings([])
    return _settings


def make_userdirs() -> None:
    """Create the QuPyt user directories of the active settings if they don't exist."""
    settings = get_settings()
    for directory in (
        settings.home_dir,
        settings.log_dir,
        settings.sequence_dir,
        settings.waiting_room_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
