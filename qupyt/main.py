"""
Main programm. Measurement loop is started from here.
"""

import argparse
import logging
import os
import platform
import threading
import time
from datetime import date
from pathlib import Path
from queue import Empty, Queue
from time import sleep
from typing import TextIO

import yaml
from tqdm import tqdm
from watchdog.events import (
    FileSystemEvent,
    PatternMatchingEventHandler,
)
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


def _set_busy(status_file: Path) -> None:
    status_file.write_text("busy", encoding="utf-8")


def _set_ready(status_file: Path) -> None:
    status_file.write_text("ready", encoding="utf-8")

def _mark_failed(path: str, original: str, timestamp: int) -> None:
    if os.path.exists(path): 
        os.replace(path, f"{original}_failed_{timestamp}")



def parse_input(
    queue: Queue[str], stop: threading.Event, config: QuPytConfig
) -> None:
    """
    Worker loop: take instruction files from the queue and run them.

    Blocks on the queue (with a timeout, so ``stop`` is checked regularly)
    instead of waiting on a separate event. When the queue runs empty after
    a measurement, devices are reset and the status is set to "ready".

    Each instruction file is renamed to ``<file>_running_<ts>`` while it
    runs, deleted after a successful measurement and renamed to
    ``<file>_failed_<ts>`` otherwise. ``status.txt`` in the workspace
    contains ``busy`` or ``ready``.

    :param queue: Paths of instruction files (filled by the event handler).
    :type queue: Queue[str]
    :param stop: Set to end the loop; all devices are released afterwards.
    :type stop: threading.Event
    :param config: Active workspace configuration.
    :type config: QuPytConfig
    """
    logger = logging.getLogger(__name__).getChild("worker")
    status_file = config.input_dir / "status.txt"
    static_devices = DeviceHandler({})
    dynamic_devices = DynamicDeviceHandler({}, number_dynamic_steps=1)
    processed_files = set()  # track files already picked from the queue
    _set_ready(status_file)

    while not stop.is_set():
        try:
            instruction_file = queue.get(timeout=1)
        except Empty:
            continue  # nothing yet; loop to re-check stop
        
        running_file: str | None = None
        file_key = None
        # Use timestamped _running filename to avoid collisions
        timestamp = int(time.time() * 1000)  # milliseconds

        try:
            _set_busy(status_file)

            # Track files by full path + last modification time
            instruction_path = Path(instruction_file)
            if not instruction_path.exists():
                # File might have been removed; skip
                continue
            file_key = (str(instruction_path), os.path.getmtime(instruction_path))
            if file_key in processed_files:
                continue  # already processed this instance
            processed_files.add(file_key)

            logger.info("STARTED NEW MEASUREMENT".ljust(65, "=") + "[START]")

            # Retry reading the file in case it's still being written
            for attempt in range(3):
                try:
                    with open(instruction_file, "r", encoding="utf-8") as file:
                        params = yaml.safe_load(file)
                    break
                except FileNotFoundError:
                    time.sleep(0.1)  # wait 100ms
            else:
                logger.warning(f"File still missing after retries, skipping: {instruction_file}")
                processed_files.discard(file_key)
                continue

            running_file = f"{instruction_file}_running_{timestamp}"
            os.replace(instruction_file, running_file)

            # Update parameters
            parameter_update = write_user_ps(
                Path(params["ps_path"]), params["pulse_sequence"]
            )
            update_params_dict(params, parameter_update)

            # Create measurement objects
            synchroniser = Synchroniser.create(
                params["synchroniser"]["type"],
                params["synchroniser"]["config"],
                params["synchroniser"]["channel_mapping"],
            )
            sensor = Sensor.create(params["sensor"]["type"], params["sensor"]["config"])

            # Update devices
            static_devices.update_devices(params["static_devices"])
            dynamic_devices.number_dynamic_steps = int(params["dynamic_steps"])
            dynamic_devices.update_devices(params["dynamic_devices"])

            # Run the measurement
            success_status = run_measurement(
                static_devices, dynamic_devices, sensor, synchroniser, params
            )

            # Handle post-measurement file renames
            if success_status == "success":
                if os.path.exists(running_file):
                    os.remove(running_file)
            else:
                _mark_failed(running_file, instruction_file, timestamp)

        except Exception:
            logger.exception("Exception in main measurement loop")
            _mark_failed(running_file or instruction_file, instruction_file, timestamp)
        finally:
            # Previously done in the "queue empty" branch before waiting.
            if queue.empty():
                static_devices.update_devices({})
                dynamic_devices.update_devices({})
                _set_ready(status_file)
            
            if file_key is not None:
                # Allow the same filename to be processed again in future
                processed_files.discard(file_key)

    # Shutting down: release all devices.
    static_devices.update_devices({})
    dynamic_devices.update_devices({})


class WaitingRoomEventHandler(PatternMatchingEventHandler):
    """
    Watch the waiting room and put new instruction files into ``queue``.

    Which file system events are used depends on the operating system:

    - Windows: modified,
    - Linux: closed (the file has been written completely),
    - macOS and others: created, modified and moved (the destination of a
      move/rename, only if it ends in ``.yaml``).

    Several events for the same file are harmless: the worker
    (``parse_input``) skips paths that no longer exist (already renamed to
    ``..._running_<ts>``) and files it is currently processing.

    Attributes:
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
        """
        Initialize the event handler with the specified parameters.

        Args:
            queue (Queue[str]): Receives the paths of instruction files.
            patterns (list, optional): List of file patterns to include in monitoring.
            ignore_patterns (list, optional): List of file patterns to exclude from monitoring.
            ignore_directories (bool, optional): Whether to ignore changes in directories. Defaults to False.
            case_sensitive (bool, optional): Whether file pattern matching is case sensitive. Defaults to False.
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
        """
        Handle the event when a monitored file is closed.

        This method is called when a file matching the specified patterns is closed.
        It puts the file path in the queue, which wakes the worker.

        Args:
            event (FileClosedEvent): The event object containing information about the closed file.
        """
        self.queue.put(os.fsdecode(event.src_path))

    def _on_modified(self, event: FileSystemEvent) -> None:
        """
        Handle the event when a monitored file is modified.

        This method is called when a file matching the specified patterns is modified.
        It waits briefly (so the writer can finish) and puts the file path
        in the queue, which wakes the worker.

        Args:
            event (FileModifiedEvent): The event object containing information about the modified file.
        """
        sleep(0.1)
        self.queue.put(os.fsdecode(event.src_path))

    def _on_created(self, event: FileSystemEvent) -> None:
        """
        Handle the event when a monitored file is created.

        This method is called when a file matching the specified patterns is created.
        It waits briefly (so the writer can finish) and puts the file path
        in the queue, which wakes the worker.

        Args:
            event: The event object containing information about the created file.
        """
        sleep(0.1)
        self.queue.put(os.fsdecode(event.src_path))

    def _on_moved(self, event: FileSystemEvent) -> None:
        """
        Handle the event when a monitored file is moved.

        This method is called when a file matching the specified patterns is moved.
        It waits briefly (so the writer can finish) and puts the destination
        path in the queue, which wakes the worker.

        Args:
            event: The event object containing information about the moved file.
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
    """
    Parse the command line (``--verbose``, ``--in-dir``, ``--out-dir``,
    ``--devices``).

    :return: Parsed arguments; ``in_dir`` / ``out_dir`` are ``None`` if not
        given, so the environment variables and defaults apply.
    :rtype: argparse.Namespace
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
        "--devices",
        action="store_true",
        help="Show all available supported device types.",
    )
    return parser.parse_args()


class _TqdmStreamHandler(logging.StreamHandler[TextIO]):
    """Console handler that writes via ``tqdm.write`` so progress bars stay intact."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=self.stream)
        # Same as logging.StreamHandler.emit: never let a logging error crash the caller.
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self.handleError(record)


def setup_logger(log_dir: Path, verbose: bool) -> None:
    """
    Configure the root logger.

    - Log file (``log_dir/log_<date>.log``): INFO and above, always,
      with the name of the module that logged.
    - Terminal: WARNING and above, always; INFO only with ``verbose``.

    :param log_dir: Directory for the log file (must exist).
    :type log_dir: Path
    :param verbose: Also show INFO messages on the terminal.
    :type verbose: bool
    """
    assert isinstance(log_dir, Path)
    datefmt = "%Y/%m/%d %I:%M:%S %p"

    logfile = log_dir / f"log_{date.today()}.log"
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(levelname)s\t%(asctime)s %(name)s %(message)s", datefmt)
    )

    console_handler = _TqdmStreamHandler()
    console_handler.setLevel(logging.INFO if verbose else logging.WARNING)

    # basicConfig only sets its format on handlers without a formatter,
    # i.e. here only on the console handler.
    logging.basicConfig(
        handlers=[file_handler, console_handler],
        format="%(levelname)s\t%(asctime)s %(message)s",
        datefmt=datefmt,
        level=logging.INFO,
        force=True,
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


def main() -> None:
    """
    Start the main measurement loop.
    """
    args = parse_user_arguments()

    if args.devices:
        _print_devices_and_exit()

    config = QuPytConfig(args.in_dir, args.out_dir)
    config.create_ws()
    set_config(config)

    setup_logger(log_dir=config.log_dir, verbose=args.verbose)
    logger = logging.getLogger(__name__)

    queue: Queue[str] = Queue()
    stop = threading.Event()

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

    print("\nWelcome to")
    print(QUPYT_LOGO)
    logger.info("Started QuPyt, watching %s", config.waiting_room)

    observer.start()
    worker.start()
    try:
        while worker.is_alive():
            sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        # Stop accepting new files, then let the worker finish the current
        # measurement (if any) and release the devices.
        observer.stop()
        stop.set()
        observer.join()
        worker.join()


if __name__ == "__main__":
    main()
