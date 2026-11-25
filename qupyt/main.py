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
from queue import Empty, Queue
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
    logfile = settings.log_dir / f"log_{date.today()}.log"
    handlers: list[logging.Handler] = [logging.FileHandler(logfile)]
    if settings.log_to_console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        handlers=handlers,
        format="%(levelname)s\t%(asctime)s %(message)s",
        datefmt="%Y/%m/%d %I:%M:%S %p",
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


def parse_input() -> None:
    static_devices = DeviceHandler({})
    dynamic_devices = DynamicDeviceHandler({}, number_dynamic_steps=1)
    processed_files = set()  # track files already picked from the queue
    while not stop.is_set():
        if queue.empty():
            static_devices.update_devices({})
            dynamic_devices.update_devices({})
            _set_ready()
            event_thread.wait(timeout=0.5)
            event_thread.clear()
            continue
        try:
            _set_busy()

            # Get the next file from the queue
            try:
                instruction_file = queue.get(timeout=1)
            except Empty:
                continue  # no file yet, check again

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
                print(f"File still missing after retries, skipping: {instruction_file}")
                processed_files.discard(file_key)
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

            # Handle post-measurement file renames
            if success_status == "success":
                if os.path.exists(running_file):
                    os.remove(running_file)
            elif success_status == "failed":
                failed_file = f"{instruction_file}_failed_{timestamp}"
                if os.path.exists(running_file):
                    os.replace(running_file, failed_file)

            # Allow the same filename to be processed again in future
            processed_files.discard(file_key)

        except Exception:
            logger.exception("Excpetion in main measurement loop")
            traceback.print_exc()


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
        It puts the file path in a queue, waits briefly, and triggers the event thread.

        Args:
            event: The event object containing information about the modified file.
        """
        queue.put(event.src_path)
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
    return my_observer


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
