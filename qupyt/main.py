"""
Main programm. Measurement loop is started from here.
"""

import logging
import os
import platform
import threading
import time
import traceback
from datetime import date
from pathlib import Path
from queue import Queue
from time import sleep

import yaml
from watchdog.events import (
    FileClosedEvent,
    FileModifiedEvent,
    PatternMatchingEventHandler,
)
from watchdog.observers import Observer

from qupyt.config import Settings, get_settings, load_settings, make_userdirs
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor.base import Sensor
from qupyt.hardware.synchroniser.base import Synchroniser
from qupyt.measurement_logic.run_measurement import run_measurement
from qupyt.pulse_sequences.pulse_sequence_handler import (
    update_params_dict,
    write_user_ps,
)
from qupyt.utils.logging import INFO, START, StatusFormatter

logger = logging.getLogger(__name__)

qupyt_logo_text = """                                                                                                        
                                                                                                         
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


def _configure_logging(settings: Settings) -> None:
    logfile: Path = settings.log_dir / f"log_{date.today()}.log"
    handlers: list[logging.Handler] = [logging.FileHandler(filename=logfile)]

    # add logging to console
    if settings.log_to_console:
        handlers.append(logging.StreamHandler())

    # add special formatter that allows logging with left justified formatting
    # from qupyt.utils.logging import FAILED, DONE, START, ...
    # called as: logger.info("<msg>", extr=FAILED)
    formatter: StatusFormatter = StatusFormatter(
        fmt="%(levelname)s\t%(asctime)s %(message)s", datefmt="%Y/%m/%d %I:%M:%S %p"
    )

    # register formatter to all handlers for consistent formatting
    for h in handlers:
        h.setFormatter(fmt=formatter)

    logging.basicConfig(
        handlers=handlers,
        encoding="utf-8",
        level=settings.log_level,
        force=True,
    )


queue: Queue[str]
event_thread: threading.Event
stop: threading.Event


def _set_busy() -> None:
    with open(get_settings().home_dir / "status.txt", "w", encoding="utf-8") as file:
        file.write("busy")


def _set_ready() -> None:
    with open(get_settings().home_dir / "status.txt", "w", encoding="utf-8") as file:
        file.write("ready")


def _read_instruction_file(path: Path) -> dict | None:
    """
    Read an instruction YAML file. Retry once in case it is still being
    written; return None (and leave the file in place) if it is still
    missing, invalid or not a mapping.
    """
    for attempt in range(2):
        if attempt:
            time.sleep(0.5)
        try:
            with open(path, "r", encoding="utf-8") as file:
                params = yaml.safe_load(file)
        except (FileNotFoundError, yaml.YAMLError) as excpt:
            problem = repr(excpt)
            continue
        if isinstance(params, dict):
            return params
        problem = f"content is {type(params).__name__}, not a mapping"
    logger.error(f"Could not read instruction file {path} ({problem}), skipping it.")
    return None


def parse_input() -> None:
    static_devices = DeviceHandler({})
    dynamic_devices = DynamicDeviceHandler({}, number_dynamic_steps=1)
    while not stop.is_set():
        if queue.empty():
            static_devices.update_devices({})
            dynamic_devices.update_devices({})
            _set_ready()
            event_thread.wait(timeout=0.5)
            event_thread.clear()
            continue
        running_file = None
        success_status = "failed"
        try:
            _set_busy()

            # This is the only consumer, so the queue is not empty here
            instruction_file = queue.get()
            instruction_path = Path(instruction_file)
            if not instruction_path.exists():
                # Already processed (duplicate event) or removed; skip
                continue

            logger.info("STARTED NEW MEASUREMENT", extra=START)

            params = _read_instruction_file(instruction_path)
            if params is None:
                continue

            # Use timestamped _running filename to avoid collisions
            timestamp = int(time.time() * 1000)  # milliseconds
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

        except Exception:
            logger.exception("Exception in main measurement loop")
            traceback.print_exc()

        finally:
            # Rename the instruction file by outcome, also if anything raised
            if running_file is not None and os.path.exists(running_file):
                if success_status == "success":
                    os.remove(running_file)
                else:
                    os.replace(running_file, f"{instruction_file}_failed_{timestamp}")


class WaitingRoomEventHandler(PatternMatchingEventHandler):
    """
    A custom event handler for monitoring changes in specific files.

    This handler extends PatternMatchingEventHandler to react to file modifications
    and closures. It uses different methods based on the operating system:
    - On Windows, it handles file modifications.
    - On other systems, it handles file closures.

    Attributes:
        patterns (list): list of file patterns to include in monitoring.
        ignore_patterns (list): list of file patterns to exclude from monitoring.
        ignore_directories (bool): Whether to ignore changes in directories.
        case_sensitive (bool): Whether file pattern matching is case sensitive.
    """

    def __init__(
        self,
        patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        ignore_directories: bool = False,
        case_sensitive: bool = False,
    ):
        """
        Initialize the event handler with the specified parameters.

        Args:
            patterns (list, optional): list of file patterns to include in monitoring.
            ignore_patterns (list, optional): list of file patterns to exclude from monitoring.
            ignore_directories (bool, optional): Whether to ignore changes in directories. Defaults to False.
            case_sensitive (bool, optional): Whether file pattern matching is case sensitive. Defaults to False.
        """
        super().__init__(
            patterns=patterns,
            ignore_patterns=ignore_patterns,
            ignore_directories=ignore_directories,
            case_sensitive=case_sensitive,
        )
        if platform.system() == "Windows":
            self.on_modified = self._on_modified
        elif platform.system() == "Linux":
            self.on_closed = self._on_closed
        else:  # macOS or other
            self.on_created = self._on_created
            self.on_modified = self._on_modified
        # A rename into the waiting room emits only a moved event.
        self.on_moved = self._on_moved

    def _on_closed(self, event: FileClosedEvent) -> None:
        """
        Handle the event when a monitored file is closed.

        This method is called when a file matching the specified patterns is closed.
        It puts the file path in a queue and triggers the event thread.

        Args:
            event (FileClosedEvent): The event object containing information about the closed file.
        """
        queue.put(event.src_path)
        event_thread.set()

    def _on_modified(self, event: FileModifiedEvent) -> None:
        """
        Handle the event when a monitored file is modified.

        This method is called when a file matching the specified patterns is modified.
        It puts the file path in a queue, waits briefly, and triggers the event thread.

        Args:
            event (FileModifiedEvent): The event object containing information about the modified file.
        """
        queue.put(event.src_path)
        sleep(0.1)
        event_thread.set()

    def _on_created(self, event) -> None:
        """
        Handle the event when a monitored file is created.

        This method is called when a file matching the specified patterns is created.
        It puts the file path in a queue, waits briefly, and triggers the event thread.

        Args:
            event: The event object containing information about the modified file.
        """
        queue.put(event.src_path)
        sleep(0.1)
        event_thread.set()

    def _on_moved(self, event) -> None:
        """
        Handle the event when a monitored file is moved.

        This method is called when a file matching the specified patterns is moved.
        It puts the destination path in a queue if it is a YAML file (e.g. an atomic
        write renaming ``job.tmp`` to ``job.yaml``), waits briefly, and triggers the
        event thread. Moves away from a YAML name (e.g. to ``*_running_*``) are ignored.

        Args:
            event: The event object containing information about the moved file.
        """
        if Path(event.dest_path).suffix != ".yaml":
            return
        queue.put(event.dest_path)
        sleep(0.1)
        event_thread.set()


def _get_observer_event_hanlder() -> WaitingRoomEventHandler:
    patterns = ["**/*.yaml"]
    ignore_patterns = None
    ignore_directories = True
    case_sensitive = True
    return WaitingRoomEventHandler(
        patterns, ignore_patterns, ignore_directories, case_sensitive
    )


def _get_observer(event_handler: WaitingRoomEventHandler) -> Observer:
    path = get_settings().waiting_room_dir
    go_recursively = False
    my_observer = Observer()
    my_observer.schedule(event_handler, path, recursive=go_recursively)
    logger.debug(msg=f"Waiting for files in: {path}", extra=INFO)
    return my_observer


def _queue_leftover_files(waiting_room: Path) -> None:
    """
    Handle files left in the waiting room by a previous session: mark
    interrupted runs (``*_running_*``) as failed and queue pending YAML
    files, oldest first.
    """
    for running_file in waiting_room.glob("*_running_*"):
        failed_file = running_file.with_name(
            running_file.name.replace("_running_", "_failed_", 1)
        )
        os.replace(running_file, failed_file)
        logger.warning(f"Interrupted run {running_file.name} marked as failed.")
    for yaml_file in sorted(waiting_room.glob("*.yaml"), key=os.path.getmtime):
        queue.put(str(yaml_file))


def main() -> None:
    """
    Start the main measurement loop.
    """
    settings = load_settings()
    print("\nWelcome to")
    print(qupyt_logo_text)
    make_userdirs()
    _configure_logging(settings)
    logger.info("Started Program")
    global event_thread
    event_thread = threading.Event()
    global stop
    stop = threading.Event()
    global queue
    queue = Queue()
    event_handler = _get_observer_event_hanlder()
    observer = _get_observer(event_handler)
    thread = threading.Thread(target=parse_input)
    observer.start()
    _queue_leftover_files(settings.waiting_room_dir)
    thread.start()
    try:
        while True:
            sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stop.set()
        observer.stop()
        observer.join()
        thread.join()


if __name__ == "__main__":
    main()
