"""Command-line failures must explain what failed without requiring --verbose."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from qupyt.set_up import get_config


def _run_cli(
    tmp_path: Path, source: str, *, setup: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``source`` in a fresh interpreter with an isolated workspace.

    With ``setup`` the workspace and logging are configured the way ``main()``
    does it (terminal: short messages, log file under ``<tmp>/.qupyt/log``).
    """
    script = """
import logging
from pathlib import Path
import sys
from unittest.mock import Mock, patch

home = Path(sys.argv[1])
"""
    if setup:
        script += """
import qupyt.main as service
from qupyt.set_up import QuPytConfig, set_config, get_waiting_room
config = QuPytConfig(home / ".qupyt", home)
config.create_ws()
set_config(config)
service.setup_logger(config.log_dir, verbose=False)
"""
    script += textwrap.dedent(source)
    env = {
        **os.environ,
        "QUPYT_IN_DIR": str(tmp_path / ".qupyt"),
        "QUPYT_OUT_DIR": str(tmp_path),
    }
    return subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
        env=env,
    )


def test_cli_startup_and_mock_acquisition_work_without_basler_driver(
    tmp_path: Path,
) -> None:
    result = _run_cli(
        tmp_path,
        """
        with patch.dict(sys.modules, {"pypylon": None, "pypylon.pylon": None}):
            from qupyt.hardware.sensor import Sensor
            sensor = Sensor.create(
                "MockCam", {"number_measurements": 2, "image_roi": [2, 3]}
            )
            sensor.open()
            try:
                assert sensor.acquire_data().shape == (2, 2, 3)
            finally:
                sensor.close()
            try:
                Sensor.create("Basler1920", {})
            except ImportError as error:
                # Lazy import: a clear ImportError chained to the missing module.
                assert error.__cause__.name == "pypylon"
            else:
                raise AssertionError("BaslerCam must require its driver")
        logging.shutdown()
        """,
    )
    assert result.returncode == 0, result.stderr
    assert "SwigPyPacked" not in result.stderr
    assert "SwigPyObject" not in result.stderr
    assert "swigvarlink" not in result.stderr


@pytest.mark.parametrize("width", ["0", "20"])
def test_cli_rejects_log_width_too_small_for_continuation_indent(
    tmp_path: Path, width: str
) -> None:
    result = _run_cli(
        tmp_path,
        f"""
        sys.argv = ["qupyt", "--log-width", {width!r}]
        import qupyt.main
        qupyt.main.main()
        """,
        setup=False,
    )
    assert result.returncode == 2
    assert "--log-width must be at least 21 columns" in result.stderr


def test_default_cli_reports_malformed_instruction_and_recovery_path(
    tmp_path: Path,
) -> None:
    result = _run_cli(
        tmp_path,
        """
        instruction = get_waiting_room() / "malformed.yaml"
        instruction.write_text("sensor: [unterminated", encoding="utf-8")
        try:
            service._process_instruction(instruction, Mock(), Mock())
        except Exception:
            pass
        logging.shutdown()
        """,
    )
    assert result.returncode == 0, result.stderr
    retained = list((tmp_path / ".qupyt" / "waiting_room").glob("*_failed_*"))
    logs = list((tmp_path / ".qupyt" / "log").glob("*.log"))
    assert len(retained) == len(logs) == 1
    assert "malformed.yaml" in result.stderr
    assert "ParserError" in result.stderr
    assert str(retained[0]) in result.stderr
    assert str(logs[0]) in result.stderr
    assert "Traceback (most recent call last)" not in result.stderr
    assert "Traceback (most recent call last)" in logs[0].read_text(encoding="utf-8")


def test_default_cli_reports_failed_measurement_status_and_original_cause(
    tmp_path: Path,
) -> None:
    result = _run_cli(
        tmp_path,
        """
        instruction = get_waiting_room() / "camera.yaml"
        instruction.write_text("{}", encoding="utf-8")
        service._prepare_measurement = Mock(return_value=(Mock(), Mock()))
        def fail_measurement(static, dynamic, sensor, sync, params):
            params["measurement_error"] = {
                "type": "OSError",
                "message": "camera connection lost",
                "stage": "sensor acquisition",
            }
            return "failed"
        service.run_measurement = fail_measurement
        service._process_instruction(instruction, Mock(), Mock())
        logging.shutdown()
        """,
    )
    assert result.returncode == 0, result.stderr
    retained = next((tmp_path / ".qupyt" / "waiting_room").glob("*_failed_*"))
    logfile = next((tmp_path / ".qupyt" / "log").glob("*.log"))
    assert "camera.yaml" in result.stderr
    assert "OSError" in result.stderr
    assert "camera connection lost" in result.stderr
    assert "sensor acquisition" in result.stderr
    assert str(retained) in result.stderr
    assert str(logfile) in result.stderr


@pytest.mark.parametrize("failure_at", ["directories", "logfile"])
def test_cli_startup_failure_reports_cause_before_logging_is_available(
    tmp_path: Path, failure_at: str
) -> None:
    setup = (
        'patch("qupyt.set_up.QuPytConfig.create_ws", side_effect=PermissionError("user directory denied"))'
        if failure_at == "directories"
        else 'patch.object(logging, "FileHandler", side_effect=PermissionError("logfile denied"))'
    )
    result = _run_cli(
        tmp_path,
        f"""
        sys.argv = ["qupyt"]
        import qupyt.main
        with {setup}:
            qupyt.main.main()
        """,
        setup=False,
    )
    assert result.returncode != 0
    assert "PermissionError" in result.stderr
    assert (
        "user directory denied" if failure_at == "directories" else "logfile denied"
    ) in result.stderr
    assert "QuPyt" in result.stderr


@pytest.mark.parametrize("failed_component", ["observer", "emitter"])
def test_service_reports_dead_instruction_watcher(
    service_main: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed_component: str,
) -> None:
    service = service_main
    emitter = Mock(is_alive=Mock(return_value=failed_component != "emitter"))
    observer = Mock(
        is_alive=Mock(return_value=failed_component != "observer"), emitters={emitter}
    )
    worker = Mock(is_alive=Mock(return_value=True))
    monkeypatch.setattr(service, "_get_observer", Mock(return_value=observer))
    monkeypatch.setattr(service.threading, "Thread", Mock(return_value=worker))
    monkeypatch.setattr(service, "_join_service_thread", Mock())
    # A missed health check must fail the test instead of leaving it running.
    monkeypatch.setattr(service, "sleep", Mock(side_effect=[None, KeyboardInterrupt]))

    component_name = "observer" if failed_component == "observer" else "watcher"
    with pytest.raises(RuntimeError, match=f"(?i){component_name}"):
        service.main()

    assert component_name in caplog.text.lower()
    observer.stop.assert_called_once()


def test_event_handler_initialization_failure_is_logged(
    service_main: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = service_main
    error = ValueError("invalid waiting-room event filters")
    monkeypatch.setattr(service, "WaitingRoomEventHandler", Mock(side_effect=error))

    with pytest.raises(ValueError) as caught:
        service.main()

    assert caught.value is error
    assert "invalid waiting-room event filters" in caplog.text


def test_worker_initialization_failure_is_logged(
    service: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = OSError("cannot initialize device handler")
    monkeypatch.setattr(service, "DeviceHandler", Mock(side_effect=error))

    with pytest.raises(OSError) as caught:
        service.parse_input(service.queue, threading.Event(), get_config())

    assert caught.value is error
    assert "cannot initialize device handler" in caplog.text


def test_fatal_worker_system_exit_is_reported_on_default_cli(tmp_path: Path) -> None:
    result = _run_cli(
        tmp_path,
        """
        import threading
        from queue import Queue
        service.DeviceHandler = Mock(side_effect=SystemExit("driver terminated initialization"))
        try:
            service.parse_input(Queue(), threading.Event(), config)
        except SystemExit:
            pass
        logging.shutdown()
        """,
    )
    assert result.returncode == 0, result.stderr
    assert "SystemExit" in result.stderr
    assert "driver terminated initialization" in result.stderr
    assert "worker" in result.stderr.lower()


def test_join_reports_ongoing_shutdown_without_abandoning_worker(
    service: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    worker = Mock(ident=123, is_alive=Mock(side_effect=[True, False]))
    service._join_service_thread(worker, "measurement worker")

    output = capsys.readouterr()
    assert "measurement worker" in (output.err + output.out).lower()
    assert worker.join.call_count == 2
    assert all(call.kwargs.get("timeout") for call in worker.join.call_args_list)
