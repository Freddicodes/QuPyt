"""Types for shared fixtures; the implementations remain in QuPyt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, TypeAlias
from unittest.mock import Mock

from qupyt import set_up
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.measurement_logic.data_handling import Data
from qupyt.set_up import QuPytConfig, set_config


@dataclass
class MeasurementEnvironment:
    static: DeviceHandler
    dynamic: DynamicDeviceHandler
    sensor: Sensor
    acquisition: Mock
    synchroniser: Synchroniser
    params: dict[str, Any]
    directory: Path
    sensor_close: Mock
    synchroniser_close: Mock
    sequence_upload: Mock | None = None


class NumericDataFactory(Protocol):
    def __call__(
        self, mode: str = "spread", compressed: bool = False, chunk_size: int = 0
    ) -> Data: ...


PipelineFixture: TypeAlias = tuple[ModuleType, dict[str, Any], Mock, Mock, Mock]
SequenceDirectory: TypeAlias = tuple[Path, dict[str, Any], Path]


def use_workspace(monkeypatch: Any, root: Path) -> QuPytConfig:
    """Activate a QuPyt workspace under ``root`` for one test.

    The workspace is ``root / ".qupyt"`` (like the default ``~/.qupyt``) and
    measurement output goes to ``root``. The previous active config is
    restored after the test.
    """
    monkeypatch.setattr(set_up, "_config", set_up._config)
    config = QuPytConfig(root / ".qupyt", root)
    config.create_ws()
    set_config(config)
    return config


def install_fake_spinapi(monkeypatch: Any, api: Any) -> None:
    """Make ``import qupyt.hardware.wrappers.spinapi_adapted`` return ``api``.

    PulseBlaster imports the wrapper lazily in ``__init__``; the parent
    package attribute is checked before ``sys.modules``, so patch both.
    """
    import sys

    import qupyt.hardware.wrappers

    monkeypatch.setitem(sys.modules, "qupyt.hardware.wrappers.spinapi_adapted", api)
    monkeypatch.setattr(qupyt.hardware.wrappers, "spinapi_adapted", api, raising=False)


def run_worker_until_idle(
    monkeypatch: Any, service: ModuleType, timeout: float = 10
) -> None:
    """Run ``service.parse_input`` on ``service.queue`` until the queue is drained.

    The worker sets the status to "ready" at start-up and again whenever the
    queue is empty after an item; the second call stops it. (Setting ``stop``
    beforehand would end the worker before the queue is processed: Ctrl+C only
    finishes the current measurement.)
    """
    import threading

    stop = threading.Event()
    ready_calls: list[Path] = []
    real_set_ready = service._set_ready

    def set_ready(status_file: Path) -> None:
        real_set_ready(status_file)
        ready_calls.append(status_file)
        if len(ready_calls) >= 2:
            stop.set()

    monkeypatch.setattr(service, "_set_ready", set_ready)
    failures: list[BaseException] = []

    def target() -> None:
        try:
            service.parse_input(service.queue, stop, set_up.get_config())
        except BaseException as error:  # re-raised in the test thread
            failures.append(error)

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(timeout=timeout)
    assert not worker.is_alive(), "worker did not finish"
    if failures:
        raise failures[0]
