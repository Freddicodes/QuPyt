"""
Location of the QuPyt workspace on the user's machine.

The workspace is a directory (``~/.qupyt`` by default) containing the log
files, the generated pulse sequences and the waiting room that QuPyt
watches for measurement instruction files. Measurement data is written to a
separate output directory (the current working directory by default).

:class:`QuPytConfig` describes one workspace. The module keeps one active
config, which ``main()`` replaces via :func:`set_config` at startup. Code
that is used without ``main()`` (e.g. standalone pulse sequence scripts)
gets the default workspace. The ``get_*`` functions always return paths
of the currently active config.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_INPUT_DIR_ENV_VAR = "QUPYT_IN_DIR"
_DEFAULT_INPUT_DIR = Path.home() / ".qupyt"

_OUTPUT_DIR_ENV_VAR = "QUPYT_OUT_DIR"


class QuPytConfig:
    """
    Locations of the QuPyt workspace and its subdirectories.

    The workspace (input) directory is chosen in this order:
        1. the ``in_dir`` argument (e.g. from ``--in-dir``),
        2. the ``QUPYT_IN_DIR`` environment variable,
        3. ``~/.qupyt``.

    The output directory for measurement data is chosen in this order:
        1. the ``output_dir`` key of a measurement YAML file (per
           measurement, applied in ``run_measurement``),
        2. the ``output_dir`` argument (e.g. from ``--out-dir``),
        3. the ``QUPYT_OUT_DIR`` environment variable,
        4. the current working directory when the config is created.
    """

    def __init__(self, in_dir: Path | str | None = None, output_dir: Path | str | None = None) -> None:
        """
        Determine the workspace paths. Nothing is created on disk;
        call :meth:`create_ws` for that.

        :param in_dir: Workspace directory. If omitted, ``QUPYT_IN_DIR``
            or ``~/.qupyt`` is used. ``~`` is expanded and relative paths
            are made absolute (relative to the working directory at startup).
        :type in_dir: Path | str | None
        :param output_dir: Output directory. If omitted, ``QUPYT_OUT_DIR``
            or the current directory is used. ``~`` is expanded and relative
            paths are made absolute. A measurement YAML can still override it
            with its own ``output_dir`` key.
        :type output_dir: Path | str | None
        """
        chosen_input_dir = in_dir or os.getenv(_INPUT_DIR_ENV_VAR) or _DEFAULT_INPUT_DIR
        self.input_dir: Path = Path(chosen_input_dir).expanduser().resolve()

        # The current directory is looked up here, not at import time.
        chosen_output_dir = output_dir or os.getenv(_OUTPUT_DIR_ENV_VAR) or Path.cwd()
        self.output_dir: Path = Path(chosen_output_dir).expanduser().resolve()

        self.log_dir: Path = self.input_dir / "log"
        self.seq_dir: Path = self.input_dir / "sequences"
        self.waiting_room: Path = self.input_dir / "waiting_room"

    def create_ws(self) -> None:
        """
        Create the workspace, its subdirectories and the output directory
        if they don't exist yet.

        :raises OSError: if a directory cannot be created. QuPyt cannot run
            without its workspace, so the error is not swallowed.
        """
        for directory in (self.input_dir, self.log_dir, self.seq_dir, self.waiting_room, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)
        logger.info("Workspace ready at %s", self.input_dir)


_config = QuPytConfig()


def set_config(config: QuPytConfig) -> None:
    """
    Make ``config`` the active workspace for all ``get_*`` functions.

    :param config: The workspace configuration to activate.
    :type config: QuPytConfig
    """
    global _config
    _config = config


def get_config() -> QuPytConfig:
    """
    :return: The currently active workspace configuration.
    :rtype: QuPytConfig
    """
    return _config


def get_seq_dir() -> Path:
    """
    :return: Directory to which pulse sequences are written and read.
    :rtype: Path
    """
    return _config.seq_dir


def get_log_dir() -> Path:
    """
    :return: Directory for the log files.
    :rtype: Path
    """
    return _config.log_dir


def get_waiting_room() -> Path:
    """
    :return: Directory watched for measurement instruction files.
    :rtype: Path
    """
    return _config.waiting_room


def get_input_dir() -> Path:
    """
    :return: The workspace directory itself (contains ``status.txt``).
    :rtype: Path
    """
    return _config.input_dir


def get_output_dir() -> Path:
    """
    :return: Directory used for output of the measurement files.
    :rtype: Path
    """
    return _config.output_dir


