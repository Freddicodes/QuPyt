"""qupyt.main: command line arguments and logger setup.

setup_logger: the file gets INFO and above, the terminal WARNING and above
(INFO with --verbose).
"""

import logging
import platform
import sys
from datetime import date
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

import pytest

import qupyt.main as qupyt_main
from qupyt.main import WaitingRoomEventHandler, parse_user_arguments, setup_logger

# --- command line -------------------------------------------------------------


def parse(monkeypatch: pytest.MonkeyPatch, *argv: str) -> Any:
    monkeypatch.setattr(sys, "argv", ["qupyt", *argv])
    return parse_user_arguments()


def test_cli_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    args = parse(monkeypatch)
    # None, so QUPYT_IN_DIR / QUPYT_OUT_DIR and the defaults can take effect.
    assert args.in_dir is None
    assert args.out_dir is None
    assert args.verbose is False


@pytest.mark.parametrize(
    "in_flag, out_flag, verbose_flag",
    [("--in-dir", "--out-dir", "--verbose"), ("-in", "-out", "-v")],
)
def test_cli_dirs_and_verbose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    in_flag: Any,
    out_flag: Any,
    verbose_flag: Any,
) -> None:
    args = parse(
        monkeypatch,
        in_flag,
        str(tmp_path / "in"),
        out_flag,
        str(tmp_path / "out"),
        verbose_flag,
    )
    assert args.in_dir == tmp_path / "in"
    assert args.out_dir == tmp_path / "out"
    assert isinstance(args.in_dir, Path)
    assert isinstance(args.out_dir, Path)
    assert args.verbose is True


# --- logger -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_root_logger() -> Any:
    """setup_logger reconfigures the root logger; put pytest's handlers back afterwards."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def _emit_one_of_each() -> None:
    log = logging.getLogger("qupyt.test_main")
    log.info("info-message")
    log.warning("warning-message")
    log.error("error-message")


def _logfile_text(log_dir: Any) -> Any:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return (log_dir / f"log_{date.today()}.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("verbose", [False, True])
def test_logfile_always_gets_info_and_above(tmp_path: Path, verbose: Any) -> None:
    setup_logger(tmp_path, verbose=verbose)
    _emit_one_of_each()
    text = _logfile_text(tmp_path)
    assert "info-message" in text
    assert "warning-message" in text
    assert "error-message" in text


def test_terminal_without_verbose_shows_only_warnings_and_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logger(tmp_path, verbose=False)
    _emit_one_of_each()
    err = capsys.readouterr().err
    assert "info-message" not in err
    assert "warning-message" in err
    assert "error-message" in err


def test_terminal_with_verbose_also_shows_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logger(tmp_path, verbose=True)
    _emit_one_of_each()
    err = capsys.readouterr().err
    assert "info-message" in err
    assert "warning-message" in err


def test_logfile_is_utf8(tmp_path: Path) -> None:
    setup_logger(tmp_path, verbose=False)
    logging.getLogger("qupyt.test_main").info("pulse 2 µs")
    assert "pulse 2 µs" in _logfile_text(tmp_path)


def test_exception_traceback_reaches_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logger(tmp_path, verbose=False)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("qupyt.test_main").exception("measurement failed")
    err = capsys.readouterr().err
    assert "measurement failed" in err
    assert "RuntimeError: boom" in err


# --- waiting room events ------------------------------------------------------


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(qupyt_main, "sleep", lambda _seconds: None)
    return WaitingRoomEventHandler(
        Queue(), patterns=["**/*.yaml"], ignore_directories=True
    )


def moved(src: str, dest: str) -> Any:
    return SimpleNamespace(src_path=src, dest_path=dest)


def test_on_moved_queues_the_new_location(handler: Any) -> None:
    # e.g. an editor writing foo.tmp and renaming it to foo.yaml
    handler._on_moved(moved("/wr/foo.tmp", "/wr/foo.yaml"))
    assert handler.queue.get_nowait() == "/wr/foo.yaml"


@pytest.mark.parametrize(
    "dest", ["/wr/foo.yaml_running_123", "/wr/foo.yaml_failed_123"]
)
def test_on_moved_ignores_qupyts_own_renames(handler: Any, dest: Any) -> None:
    handler._on_moved(moved("/wr/foo.yaml", dest))
    assert handler.queue.empty()


# Which watchdog hooks queue a file, per platform (same as before the typed rewrite).
HOOKS_PER_PLATFORM = {
    "Windows": {"on_modified"},
    "Linux": {"on_closed"},
    "Darwin": {"on_created", "on_modified", "on_moved"},
}


@pytest.mark.parametrize("system", sorted(HOOKS_PER_PLATFORM))
@pytest.mark.parametrize("hook", ["on_closed", "on_modified", "on_created", "on_moved"])
def test_hook_wiring_per_platform(
    monkeypatch: pytest.MonkeyPatch, system: Any, hook: Any
) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(qupyt_main, "sleep", lambda _seconds: None)
    handler = WaitingRoomEventHandler(
        Queue(), patterns=["**/*.yaml"], ignore_directories=True
    )
    getattr(handler, hook)(moved("/wr/foo.yaml", "/wr/foo.yaml"))
    assert handler.queue.empty() is (hook not in HOOKS_PER_PLATFORM[system])


def test_bytes_paths_are_decoded(handler: Any) -> None:
    handler._on_created(SimpleNamespace(src_path=b"/wr/foo.yaml"))
    assert handler.queue.get_nowait() == "/wr/foo.yaml"
