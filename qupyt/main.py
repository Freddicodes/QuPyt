"""Main program. The measurement service is started from here.

The waiting room is watched for measurement instruction files (YAML). A worker
thread takes them from a queue one at a time, runs the measurement and keeps
failed instruction files for inspection.
"""

import argparse
import logging
import os
import platform
import sys
import textwrap
import threading
import time
from datetime import date
from functools import partial
from pathlib import Path
from queue import Empty, Queue
from time import sleep
from typing import Any, TextIO

import yaml
from tqdm import tqdm
from watchdog.events import FileSystemEvent, PatternMatchingEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.measurement_logic.run_measurement import run_measurement
from qupyt.pulse_sequences.pulse_sequence_handler import (
    update_params_dict,
    write_user_ps,
)
from qupyt.set_up import QuPytConfig, set_config
from qupyt.utils.errors import cleanup_after_error, cleanup_all, cleanup_on_exit

logger = logging.getLogger(__name__)

QUPYT_LOGO = """                                                                                                        
                                                                                                         
     QQQQQQQQQ                       PPPPPPPPPPPPPPPPP                                     tttt          
   QQ:::::::::QQ                     P::::::::::::::::P                                 ttt:::t          
 QQ:::::::::::::QQ                   P::::::PPPPPP:::::P                                t:::::t          
Q:::::::QQQ:::::::Q                  PP:::::P     P:::::P                               t:::::t          
Q::::::O   Q::::::Quuuuuu    uuuuuu    P::::P     P:::::Pyyyyyyy           yyyyyyyttttttt:::::ttttttt    
Q:::::O     Q:::::Qu::::u    u::::u    P::::P     P:::::P y:::::y         y:::::y t:::::::::::::::::t    
Q:::::O     Q:::::Qu::::u    u::::u    P::::PPPPPP:::::P   y:::::y       y:::::y  t:::::::::::::::::t    
Q:::::O     Q:::::Qu::::u    u::::u    P:::::::::::::PP     y:::::y     y:::::y   tttttt:::::::tttttt    
Q:::::O     Q:::::Qu::::u    u::::u    P::::PPPPPPPPP        y:::::y   y:::::y          t:::::t          
Q:::::O     Q:::::Qu::::u    u::::u    P::::P                 y:::::y y:::::y           t:::::t          
Q:::::O  QQQQ:::::Qu::::u    u::::u    P::::P                  y:::::y:::::y            t:::::t          
Q::::::O Q::::::::Qu:::::uuuu:::::u    P::::P                   y:::::::::y             t:::::t    tttttt
Q:::::::QQ::::::::Qu:::::::::::::::uuPP::::::PP                  y:::::::y              t::::::tttt:::::t
 QQ::::::::::::::Q  u:::::::::::::::uP::::::::P                   y:::::y               tt::::::::::::::t
   QQ:::::::::::Q    uu::::::::uu:::uP::::::::P                  y:::::y                  tt:::::::::::tt
     QQQQQQQQ::::QQ    uuuuuuuu  uuuuPPPPPPPPPP                 y:::::y                     ttttttttttt  
             Q:::::Q                                           y:::::y                                   
              QQQQQQ                                          y:::::y                                    
                                                             y:::::y                                     
                                                            y:::::y                                      
                                                           yyyyyyy                                       
                                                                                                         
"""


# Status lines (service started / stopping / stopped) are printed to the
# terminal unless --verbose already shows INFO messages there.
_print_status = True


def _show_service_status(message: str) -> None:
    """Log a service status line and, without --verbose, print it to stderr."""
    logger.info(message)
    if _print_status:
        print(message, file=sys.stderr, flush=True)


def _set_busy(status_file: Path) -> None:
    status_file.write_text("busy", encoding="utf-8")
    logger.debug("Measurement service status: busy")


def _set_ready(status_file: Path) -> None:
    status_file.write_text("ready", encoding="utf-8")
    logger.debug("Measurement service status: ready")


def _release_idle_devices(
    static_devices: DeviceHandler, dynamic_devices: DynamicDeviceHandler
) -> None:
    """Release both handlers' devices; one failing handler does not skip the other."""
    for handler in (static_devices, dynamic_devices):
        try:
            handler.update_devices({})
        except Exception, SystemExit:
            logger.exception(
                "Failed to release idle %s devices", type(handler).__name__
            )


def _prepare_measurement(
    params: dict[str, Any],
    static_devices: DeviceHandler,
    dynamic_devices: DynamicDeviceHandler,
) -> tuple[Sensor, Synchroniser]:
    """Generate the pulse sequences and create the hardware for one measurement.

    If a step fails, the sensor/synchroniser created so far are closed and
    the devices released before the error is re-raised.

    Parameters
    ----------
    params : dict[str, Any]
        Measurement parameters; updated with the values returned by the
        user's pulse sequence script.
    static_devices : DeviceHandler
        Handler for the static devices.
    dynamic_devices : DynamicDeviceHandler
        Handler for the swept devices.

    Returns
    -------
    tuple[Sensor, Synchroniser]
        The created sensor and synchroniser.
    """
    synchroniser: Synchroniser | None = None
    sensor: Sensor | None = None
    preparing_hardware = False
    stage = "pulse sequence generation"
    try:
        parameter_update = write_user_ps(
            Path(params["ps_path"]), params["pulse_sequence"]
        )
        update_params_dict(params, parameter_update)
        preparing_hardware = True
        stage = "synchroniser creation"
        synchroniser = Synchroniser.create(
            params["synchroniser"]["type"],
            params["synchroniser"]["config"],
            params["synchroniser"]["channel_mapping"],
        )
        stage = "sensor creation"
        sensor = Sensor.create(params["sensor"]["type"], params["sensor"]["config"])
        stage = "device configuration"
        static_devices.update_devices(params["static_devices"])
        dynamic_devices.number_dynamic_steps = int(params["dynamic_steps"])
        dynamic_devices.update_devices(params["dynamic_devices"])
    except BaseException:
        logger.exception("Measurement preparation failed during %s", stage)
        for name, resource in (("sensor", sensor), ("synchroniser", synchroniser)):
            if resource is not None:
                cleanup_after_error(resource.close, f"{name} preparation cleanup")
        if preparing_hardware:
            for handler in (static_devices, dynamic_devices):
                cleanup_after_error(
                    partial(handler.update_devices, {}),
                    f"{type(handler).__name__} preparation cleanup",
                )
        raise
    logger.info("Hardware preparation completed")
    return sensor, synchroniser


def _process_instruction(
    instruction_file: Path,
    static_devices: DeviceHandler,
    dynamic_devices: DynamicDeviceHandler,
) -> None:
    """Run one instruction file.

    The file is renamed to ``<file>_running_<ts>`` while it runs, deleted
    after a successful measurement and renamed to ``<file>_failed_<ts>``
    after any failure (also if preparation or reading the YAML fails).

    Parameters
    ----------
    instruction_file : pathlib.Path
        The instruction YAML in the waiting room.
    static_devices : DeviceHandler
        Handler for the static devices.
    dynamic_devices : DynamicDeviceHandler
        Handler for the swept devices.
    """
    timestamp = time.time_ns()
    running_file = Path(f"{instruction_file}_running_{timestamp}")
    failed_file = Path(f"{instruction_file}_failed_{timestamp}")
    claimed = False
    stage = "claiming the instruction file"
    try:
        os.replace(instruction_file, running_file)
        claimed = True
        logger.info(
            "Claimed instruction file: %s -> %s", instruction_file, running_file
        )
        stage = "reading YAML instructions"
        with running_file.open("r", encoding="utf-8") as file:
            params = yaml.safe_load(file)
        if not isinstance(params, dict):
            raise ValueError("Measurement instructions must contain a YAML mapping")
        stage = "measurement preparation"
        sensor, synchroniser = _prepare_measurement(
            params, static_devices, dynamic_devices
        )
        stage = "measurement execution"
        success_status = run_measurement(
            static_devices, dynamic_devices, sensor, synchroniser, params
        )
        if success_status == "success":
            stage = "removing the completed instruction file"
            running_file.unlink()
            logger.info("Removed completed instruction file %s", running_file)
        else:
            # Report the primary cause before moving the file.
            detail = params.get("measurement_error", {})
            logger.error(
                "Instruction %s failed during %s: %s: %s",
                instruction_file,
                detail.get("stage", "measurement execution"),
                detail.get("type", "MeasurementError"),
                detail.get(
                    "message", "No failure details returned; check the log file"
                ),
            )
            stage = "retaining the failed instruction file"
            os.replace(running_file, failed_file)
            logger.error("Retained failed instruction file at %s", failed_file)
    except BaseException:
        logger.exception(
            "Exception while processing measurement instructions %s during %s",
            instruction_file,
            stage,
        )
        if claimed:

            def retain_failed_instruction() -> None:
                if running_file.exists():
                    os.replace(running_file, failed_file)
                    logger.error("Retained failed instruction file at %s", failed_file)

            cleanup_after_error(
                retain_failed_instruction, "failed instruction retention"
            )
        raise


def parse_input(queue: Queue[str], stop: threading.Event, config: QuPytConfig) -> None:
    """Worker loop: take instruction files from the queue and run them.

    Blocks on the queue (with a timeout, so ``stop`` is checked regularly).
    When the queue runs empty after an instruction, devices are released and
    the status is set to "ready". A failing instruction is logged and the
    loop continues; on ``stop`` it ends after the current instruction.

    ``status.txt`` in the workspace contains ``busy`` or ``ready``.

    Parameters
    ----------
    queue : Queue[str]
        Paths of instruction files (filled by the event handler and the
        startup scan).
    stop : threading.Event
        Set to end the loop; all devices are released afterwards.
    config : QuPytConfig
        Active workspace configuration.
    """
    status_file = config.input_dir / "status.txt"
    try:
        static_devices = DeviceHandler({})
        dynamic_devices = DynamicDeviceHandler({}, number_dynamic_steps=1)
    except BaseException:
        logger.exception("Measurement worker could not initialize its device handlers")
        raise
    processed_files: set[tuple[str, float]] = set()
    _set_ready(status_file)
    try:
        while not stop.is_set():
            try:
                instruction_file = queue.get(timeout=1)
            except Empty:
                continue  # nothing yet; loop to re-check stop
            file_key: tuple[str, float] | None = None
            try:
                instruction_path = Path(instruction_file)
                if not instruction_path.exists():
                    logger.warning(
                        "Skipping instruction file that no longer exists: %s",
                        instruction_file,
                    )
                    continue
                # Several events for the same file: run it once.
                file_key = (str(instruction_path), os.path.getmtime(instruction_path))
                if file_key in processed_files:
                    continue
                processed_files.add(file_key)
                _set_busy(status_file)
                logger.info("STARTED NEW MEASUREMENT".ljust(65, "=") + "[START]")
                _process_instruction(instruction_path, static_devices, dynamic_devices)
            except Exception:
                # A failing instruction must not stop the worker.
                logger.exception(
                    "Instruction %s failed; QuPyt is still running and accepting "
                    "instructions.",
                    instruction_file,
                )
            finally:
                if queue.empty():
                    _release_idle_devices(static_devices, dynamic_devices)
                    _set_ready(status_file)
                if file_key is not None:
                    # Allow the same filename to be processed again in future.
                    processed_files.discard(file_key)
                queue.task_done()
    except BaseException:
        logger.exception("Measurement worker stopped unexpectedly")
        raise
    finally:
        # Shutting down: release all devices.
        _release_idle_devices(static_devices, dynamic_devices)


class WaitingRoomEventHandler(PatternMatchingEventHandler):
    """Watch the waiting room and put new instruction files into ``queue``.

    Which file system events are used depends on the operating system:

    - Windows: modified,
    - Linux: closed (the file has been written completely),
    - macOS and others: created, modified and moved (the destination of a
      move/rename, only if it ends in ``.yaml``).

    Several events for the same file are harmless: the worker
    (``parse_input``) skips paths that no longer exist (already renamed to
    ``..._running_<ts>``) and files it is currently processing.

    Attributes
    ----------
        queue (Queue[str]): Receives the paths of instruction files.
        patterns (list): List of file patterns to include in monitoring.
        ignore_patterns (list): List of file patterns to exclude from monitoring.
        ignore_directories (bool): Whether to ignore changes in directories.
        case_sensitive (bool): Whether file pattern matching is case sensitive.
    """

    def __init__(
        self,
        queue: Queue[str],
        patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        ignore_directories: bool = False,
        case_sensitive: bool = False,
    ) -> None:
        """Initialize the event handler with the specified parameters.

        Parameters
        ----------
        queue : Queue[str]
            Receives the paths of instruction files.
        patterns : list, optional
            List of file patterns to include in monitoring.
        ignore_patterns : list, optional
            List of file patterns to exclude from monitoring.
        ignore_directories : bool, optional
            Whether to ignore changes in directories. Defaults to False.
        case_sensitive : bool, optional
            Whether file pattern matching is case sensitive. Defaults to False.
        """
        super().__init__(
            patterns=patterns,
            ignore_patterns=ignore_patterns,
            ignore_directories=ignore_directories,
            case_sensitive=case_sensitive,
        )
        self.queue = queue
        self._platform = platform.system()

    # Which events are used depends on the platform (see the class docstring).
    # The on_* methods are watchdog's hooks; they only dispatch.

    def on_closed(self, event: FileSystemEvent) -> None:
        if self._platform == "Linux":
            self._on_closed(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        if self._platform != "Linux":  # Windows, macOS and other
            self._on_modified(event)

    def on_created(self, event: FileSystemEvent) -> None:
        if self._platform not in ("Windows", "Linux"):
            self._on_created(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        if self._platform not in ("Windows", "Linux"):
            self._on_moved(event)

    def _on_closed(self, event: FileSystemEvent) -> None:
        """Handle the event when a monitored file is closed.

        This method is called when a file matching the specified patterns is closed.
        It puts the file path in the queue, which wakes the worker.

        Parameters
        ----------
        event : FileClosedEvent
            The event object containing information about the closed file.
        """
        self.queue.put(os.fsdecode(event.src_path))

    def _on_modified(self, event: FileSystemEvent) -> None:
        """Handle the event when a monitored file is modified.

        This method is called when a file matching the specified patterns is modified.
        It waits briefly (so the writer can finish) and puts the file path
        in the queue, which wakes the worker.

        Parameters
        ----------
        event : FileModifiedEvent
            The event object containing information about the modified file.
        """
        sleep(0.1)
        self.queue.put(os.fsdecode(event.src_path))

    def _on_created(self, event: FileSystemEvent) -> None:
        """Handle the event when a monitored file is created.

        This method is called when a file matching the specified patterns is created.
        It waits briefly (so the writer can finish) and puts the file path
        in the queue, which wakes the worker.

        Parameters
        ----------
        event
            The event object containing information about the created file.
        """
        sleep(0.1)
        self.queue.put(os.fsdecode(event.src_path))

    def _on_moved(self, event: FileSystemEvent) -> None:
        """Handle the event when a monitored file is moved.

        This method is called when a file matching the specified patterns is moved.
        It waits briefly (so the writer can finish) and puts the destination
        path in the queue, which wakes the worker.

        Parameters
        ----------
        event
            The event object containing information about the moved file.
        """
        # Only queue moves that end in a .yaml file. QuPyt's own renames
        # (foo.yaml -> foo.yaml_running_<ts>) also arrive here because the
        # source name matches the pattern; those must not be queued.
        dest_path = os.fsdecode(event.dest_path)
        if not dest_path.endswith(".yaml"):
            return
        sleep(0.1)
        self.queue.put(dest_path)


def _get_observer(event_handler: WaitingRoomEventHandler, path: Path) -> BaseObserver:
    observer = Observer()
    observer.schedule(event_handler, str(path), recursive=False)
    return observer


def parse_user_arguments() -> argparse.Namespace:
    """Parse the command line (``--verbose``, ``--debug``, ``--log-width``, ``--in-dir``, ``--out-dir``, ``--devices``).

    Returns
    -------
    argparse.Namespace
        Parsed arguments; ``in_dir`` / ``out_dir`` are ``None`` if not
        given, so the environment variables and defaults apply.
    """
    parser = argparse.ArgumentParser(description="Start QuPyt measurement")
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Also show info messages on screen (warnings and errors are always shown)",
    )
    parser.add_argument(
        "--in-dir",
        "-in",
        type=Path,
        default=None,
        help="Workspace directory (default: $QUPYT_IN_DIR or ~/.qupyt).",
    )
    parser.add_argument(
        "--out-dir",
        "-out",
        type=Path,
        default=None,
        help="Directory for measurement data (default: $QUPYT_OUT_DIR or the current directory).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Also write DEBUG messages (acquisition and configuration details) to the log file.",
    )
    parser.add_argument(
        "--log-width",
        type=int,
        default=150,
        metavar="COLUMNS",
        help="Maximum line width in the log file and verbose output (default: 150).",
    )
    parser.add_argument(
        "--devices",
        action="store_true",
        help="Show all available supported device types.",
    )
    args = parser.parse_args()
    if args.log_width < 21:
        parser.error("--log-width must be at least 21 columns")
    return args


class _TqdmStreamHandler(logging.StreamHandler[TextIO]):
    """Console handler that writes via ``tqdm.write`` so progress bars stay intact."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=self.stream)
        # Same as logging.StreamHandler.emit: never let a logging error crash the caller.
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self.handleError(record)


class _WrappingFormatter(logging.Formatter):
    """Wrap lines longer than ``width``, indenting continuation lines."""

    def __init__(self, fmt: str, *, width: int, datefmt: str | None = None) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._width = width

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        prefix = (
            f"{record.levelname}\t{self.formatTime(record, self.datefmt)} "
            f"[{record.threadName}] "
        ).expandtabs()
        lines: list[str] = []
        for line in super().format(record).expandtabs().split("\n"):
            if len(line) <= self._width:
                lines.append(line)
                continue
            # Continuation lines line up with the message (after the prefix)
            # and keep the line's own indentation; capped for narrow widths.
            leading_space = line[: len(line) - len(line.lstrip())]
            indent = " " * min(len(prefix) + len(leading_space), self._width - 1)
            lines.extend(
                textwrap.wrap(
                    line,
                    width=self._width,
                    subsequent_indent=indent,
                    expand_tabs=False,
                    replace_whitespace=False,
                    break_on_hyphens=False,
                )
            )
        return "\n".join(lines)


class _ConsoleErrorFormatter(logging.Formatter):
    """Short terminal messages; the traceback stays in the log file."""

    def __init__(self, logfile: Path) -> None:
        super().__init__()
        self._logfile = logfile

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        message = f"{record.levelname}: {record.getMessage()}"
        if record.exc_info is not None and record.exc_info[1] is not None:
            error = record.exc_info[1]
            message += f"\n{type(error).__name__}: {error}"
        if record.levelno >= logging.ERROR:
            message += f"\nFull details: {self._logfile}"
        return message


def setup_logger(
    log_dir: Path, verbose: bool, debug: bool = False, log_width: int = 150
) -> None:
    """Configure the root logger.

    - Log file (``log_dir/log_<date>.log``): INFO and above (DEBUG with
      ``debug``), with thread, module and line number, wrapped at
      ``log_width`` columns.
    - Terminal: WARNING and above as short messages (errors with their cause
      and a pointer to the log file, tracebacks only in the file); with
      ``verbose`` INFO and above in the log file format.

    Parameters
    ----------
    log_dir : pathlib.Path
        Directory for the log file (must exist).
    verbose : bool
        Also show INFO messages on the terminal.
    debug : bool, optional
        Also write DEBUG messages to the log file.
    log_width : int, optional
        Maximum line width in the log file and verbose output.
    """
    assert isinstance(log_dir, Path)
    global _print_status
    _print_status = not verbose
    datefmt = "%Y/%m/%d %I:%M:%S %p"
    logfile = log_dir / f"log_{date.today()}.log"
    file_formatter = _WrappingFormatter(
        "%(levelname)s\t%(asctime)s [%(threadName)s] %(name)s:%(lineno)d %(message)s",
        width=log_width,
        datefmt=datefmt,
    )
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    file_handler.setFormatter(file_formatter)

    console_handler = _TqdmStreamHandler()
    if verbose:
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(file_formatter)
    else:
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(_ConsoleErrorFormatter(logfile))

    logging.basicConfig(
        handlers=[file_handler, console_handler],
        level=logging.DEBUG if debug else logging.INFO,
        force=True,
    )
    logger.info(
        "Logging initialized: file=%s level=%s", logfile, "DEBUG" if debug else "INFO"
    )


def _print_devices_and_exit() -> None:
    import sys

    from qupyt.hardware.device import Device
    from qupyt.hardware.sensor import Sensor
    from qupyt.hardware.synchroniser import Synchroniser

    sensors: list[str] = Sensor.available()
    synchronisers: list[str] = Synchroniser.available()
    devices: list[str] = Device.available()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger()

    logger.info("Devices:")
    logger.info("\tSensors:\t %s", ", ".join(sensors))
    logger.info("\tSynchroniser:\t %s", ", ".join(synchronisers))
    logger.info("\tDevices:\t %s", ", ".join(devices))

    sys.exit(0)


def _join_service_thread(thread: threading.Thread, description: str) -> None:
    """Wait for a thread, printing a status line every 5 s while it runs."""
    if thread.ident is None:
        return
    thread.join(timeout=5)
    while thread.is_alive():
        _show_service_status(
            f"Still waiting for {description} to finish. "
            "A running measurement and hardware cleanup may take time."
        )
        thread.join(timeout=5)


def _queue_existing_instructions(queue: Queue[str], waiting_room: Path) -> None:
    """Queue instruction files that were already in the waiting room at startup.

    Without this, files left behind by a previous run (e.g. still queued when
    it was stopped) would never be picked up, since the observer only reacts
    to new file events.
    """
    existing = sorted(waiting_room.glob("*.yaml"))
    for path in existing:
        queue.put(str(path))
    if existing:
        logger.info(
            "Queued %d instruction file(s) already in %s", len(existing), waiting_room
        )


def main() -> None:
    """Start the measurement service."""
    args = parse_user_arguments()

    if args.devices:
        _print_devices_and_exit()

    config = QuPytConfig(args.in_dir, args.out_dir)
    try:
        config.create_ws()
        set_config(config)
        setup_logger(
            log_dir=config.log_dir,
            verbose=args.verbose,
            debug=args.debug,
            log_width=args.log_width,
        )
    except OSError as error:
        # Logging is not available yet, so report directly on the terminal.
        print(
            "ERROR: Cannot create QuPyt directories or open the log file in "
            f"{config.input_dir}.\n{type(error).__name__}: {error}\n"
            "Check directory permissions and available disk space.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1) from error

    queue: Queue[str] = Queue()
    stop = threading.Event()
    try:
        handler = WaitingRoomEventHandler(
            queue=queue,
            patterns=["**/*.yaml"],
            ignore_patterns=None,
            ignore_directories=True,
            case_sensitive=True,
        )
        observer = _get_observer(handler, config.waiting_room)
        worker = threading.Thread(
            target=parse_input, args=(queue, stop, config), name="qupyt-worker"
        )
    except Exception:
        logger.exception("QuPyt measurement service could not start")
        raise

    print("\nWelcome to")
    print(QUPYT_LOGO)

    def shutdown() -> None:
        # Stop accepting new files, then let the worker finish the current
        # measurement (if any) and release the devices. Every step is tried;
        # the first failure is re-raised afterwards. Queued files stay in the
        # waiting room and are picked up on the next start.
        stop.set()
        cleanup_all(
            [
                ("observer stop", observer.stop),
                (
                    "observer join",
                    partial(_join_service_thread, observer, "waiting-room observer"),
                ),
                (
                    "measurement worker join",
                    partial(_join_service_thread, worker, "measurement worker"),
                ),
            ]
        )

    # On an error the shutdown still runs, but the original error is reported.
    with cleanup_on_exit(shutdown, "measurement service shutdown"):
        observer.start()
        worker.start()
        _queue_existing_instructions(queue, config.waiting_room)
        _show_service_status(
            f"QuPyt is waiting for measurement instructions in {config.waiting_room}."
        )
        try:
            while True:
                sleep(0.5)
                if not worker.is_alive():
                    raise RuntimeError(
                        "Measurement worker terminated unexpectedly; see preceding error"
                    )
                if not observer.is_alive():
                    raise RuntimeError(
                        "Waiting-room observer terminated unexpectedly; QuPyt can no "
                        f"longer accept instructions from {config.waiting_room}"
                    )
                if not all(emitter.is_alive() for emitter in observer.emitters):
                    raise RuntimeError(
                        "Waiting-room filesystem watcher terminated unexpectedly; "
                        f"check that {config.waiting_room} is accessible"
                    )
        except KeyboardInterrupt:
            _show_service_status(
                "Shutdown requested; waiting for the current measurement to finish "
                "and hardware to be released."
            )
        except BaseException:
            logger.exception("QuPyt measurement service failed")
            raise
    _show_service_status("QuPyt measurement service stopped")


if __name__ == "__main__":
    main()
