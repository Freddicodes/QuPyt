"""
main.parse_input: the worker loop around run_measurement.

Hardware creation, the user pulse sequence script and run_measurement are
replaced by fakes; the fake measurement sets ``stop`` so the loop ends after
the queued files.
"""
import threading
from queue import Queue
from unittest.mock import Mock

import pytest
import yaml

import qupyt.main as qupyt_main
from qupyt.set_up import QuPytConfig

INSTRUCTIONS = {
    "ps_path": "unused.py",
    "pulse_sequence": {},
    "synchroniser": {"type": "MockSynchroniser", "config": {}, "channel_mapping": {}},
    "sensor": {"type": "MockCam", "config": {}},
    "static_devices": {},
    "dynamic_steps": 1,
    "dynamic_devices": {},
}


@pytest.fixture
def config(tmp_path):
    config = QuPytConfig(tmp_path / "in", tmp_path / "out")
    config.create_ws()
    return config


@pytest.fixture
def fakes(monkeypatch):
    fakes = Mock()
    fakes.write_user_ps.return_value = None
    fakes.run_measurement.return_value = "success"
    monkeypatch.setattr(qupyt_main, "write_user_ps", fakes.write_user_ps)
    monkeypatch.setattr(qupyt_main, "run_measurement", fakes.run_measurement)
    monkeypatch.setattr(qupyt_main.Synchroniser, "create", fakes.create_synchroniser)
    monkeypatch.setattr(qupyt_main.Sensor, "create", fakes.create_sensor)
    return fakes


def instruction_file(config, name="odmr.yaml", content=None):
    path = config.waiting_room / name
    path.write_text(yaml.safe_dump(content or INSTRUCTIONS), encoding="utf-8")
    return path


def run_worker(monkeypatch, config, fakes, *paths):
    """
    Run parse_input until the queue is drained. parse_input calls _set_ready
    at start-up and again whenever the queue is empty after an item, so the
    second call stops the loop. Returns the status seen by each measurement.
    """
    queue: Queue[str] = Queue()
    for path in paths:
        queue.put(str(path))
    stop = threading.Event()
    ready_calls = []
    real_set_ready = qupyt_main._set_ready

    def set_ready(status_file):
        real_set_ready(status_file)
        ready_calls.append(status_file)
        if len(ready_calls) >= 2:
            stop.set()

    monkeypatch.setattr(qupyt_main, "_set_ready", set_ready)
    status_during = []
    result = fakes.run_measurement.return_value

    def measure(*_args):
        status_during.append((config.input_dir / "status.txt").read_text(encoding="utf-8"))
        return result

    fakes.run_measurement.side_effect = measure
    worker = threading.Thread(target=qupyt_main.parse_input, args=(queue, stop, config))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "worker did not stop"
    return status_during


def leftover(config):
    return sorted(p.name for p in config.waiting_room.iterdir())


def test_success_deletes_the_file_and_sets_status(monkeypatch, config, fakes):
    path = instruction_file(config)
    status_during = run_worker(monkeypatch, config, fakes, path)
    assert status_during == ["busy"]
    assert (config.input_dir / "status.txt").read_text(encoding="utf-8") == "ready"
    assert leftover(config) == []
    fakes.run_measurement.assert_called_once()
    fakes.write_user_ps.assert_called_once()


def test_failed_measurement_renames_to_failed(monkeypatch, config, fakes):
    fakes.run_measurement.return_value = "failed"
    path = instruction_file(config)
    run_worker(monkeypatch, config, fakes, path)
    (name,) = leftover(config)
    assert name.startswith("odmr.yaml_failed_")


def test_exception_renames_to_failed_and_sets_ready(monkeypatch, config, fakes):
    fakes.create_sensor.side_effect = RuntimeError("no camera")
    path = instruction_file(config)
    run_worker(monkeypatch, config, fakes, path)
    (name,) = leftover(config)
    assert name.startswith("odmr.yaml_failed_")
    assert (config.input_dir / "status.txt").read_text(encoding="utf-8") == "ready"
    fakes.run_measurement.assert_not_called()


def test_missing_file_is_skipped(monkeypatch, config, fakes):
    run_worker(monkeypatch, config, fakes, config.waiting_room / "gone.yaml")
    fakes.run_measurement.assert_not_called()


def test_same_file_queued_twice_runs_once(monkeypatch, config, fakes):
    path = instruction_file(config)
    run_worker(monkeypatch, config, fakes, path, path)
    assert fakes.run_measurement.call_count == 1


def test_parameters_from_the_pulse_sequence_script_are_merged(monkeypatch, config, fakes):
    fakes.write_user_ps.return_value = {"averages": 7}
    path = instruction_file(config)
    run_worker(monkeypatch, config, fakes, path)
    params = fakes.run_measurement.call_args.args[-1]
    assert params["averages"] == 7
