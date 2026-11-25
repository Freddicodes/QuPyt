"""
Main measurement loop.
"""

import gc
import logging
from datetime import datetime
from time import sleep
from typing import Any

import yaml
from tqdm import tqdm

from qupyt._version import __version__ as qupyt_version
from qupyt.config import get_settings
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.measurement_logic.data_handling import Data

logger = logging.getLogger(__name__)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in {"true", "yes", "1", "on"}:
            return True
        if normalized_value in {"false", "no", "0", "off"}:
            return False
    return bool(value)


def __check_average_is_divisible_by_chunks(save_in_chunks: int, averages: int) -> None:
    """
    Raise if chunked saving would split averages across dynamic steps.

    A chunk is saved every ``save_in_chunks`` averages, counted by the
    average index, which restarts at 0 for every dynamic step. If
    ``averages`` is not a multiple of ``save_in_chunks``, the leftover
    averages of one step end up in the next step's chunk (and are still
    divided by ``save_in_chunks``), and the last partial chunk is never
    saved. ``save_in_chunks == 0`` disables chunking and is always valid.
    """
    if save_in_chunks and averages % save_in_chunks != 0:
        raise ValueError(
            f"averages ({averages}) must be divisible by "
            f"data.save_in_chunks ({save_in_chunks}), otherwise chunks mix "
            "dynamic steps and the last partial chunk is lost."
        )


def run_measurement(
    static_devices: DeviceHandler,
    dynamic_devices: DynamicDeviceHandler,
    sensor: Sensor,
    synchroniser: Synchroniser,
    params: dict[str, Any],
) -> str:
    static_devices.set_all_params()
    iterator_size = int(params.get("dynamic_steps", 1))
    ps_iterator_size = int(params.get("pulse_sequence_steps", 1))
    synchroniser_params = params.get("synchroniser", {})
    upload_to_synchroniser = _as_bool(synchroniser_params.get("upload", True))
    has_dynamic_devices = bool(dynamic_devices.devices)
    mid = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    return_status = "all_fail"
    try:
        data_container = Data(params["data"])
        data_container.set_dims_from_sensor(sensor)
        data_container.create_array()
        params["filename"] = params["experiment_type"] + "_" + mid
        data_container.set_filename((params["experiment_type"], mid))
        save_in_chunks = data_container.save_in_chunks
        __check_average_is_divisible_by_chunks(
            save_in_chunks=save_in_chunks, averages=int(params["averages"])
        )

        for ps_itervalue in tqdm(range(ps_iterator_size)):
            synchroniser.open()
            synchroniser.stop()
            if upload_to_synchroniser:
                synchroniser.load_sequence(
                    get_settings().sequence_dir / f"sequence_{ps_itervalue}.yaml"
                )
            else:
                logger.info(
                    "Skipped synchroniser sequence upload".ljust(65, ".") + "[done]"
                )
            synchroniser.run()
            sleep(0.1)
            sensor.open()
            sleep(0.1)
            for itervalue in tqdm(
                range(iterator_size), leave=(ps_itervalue == ps_iterator_size - 1)
            ):
                dynamic_devices.next_dynamic_step()
                if has_dynamic_devices:
                    sleep(0.1)
                for avg in tqdm(
                    range(int(params["averages"])),
                    leave=(itervalue == (iterator_size - 1))
                    and (ps_itervalue == (ps_iterator_size - 1)),
                ):
                    sleep(float(params.get("sleep", 0)))
                    data = sensor.acquire_data(synchroniser)
                    data_container.update_data(data, ps_itervalue, itervalue, avg)
            dynamic_devices.current_dynamic_step = 0
        return_status = "success"
    except Exception as e:
        print(f"exc {e}")
        logger.exception("An error occured during the measurement!")
        return_status = "failed"
    finally:
        try:
            sensor.close()
        except Exception:
            logger.exception("Failed to close the sensor connection")

        try:
            synchroniser.close()
        except Exception:
            logger.exception("Failed to close the synchroniser connection")

        params["measurement_status"] = return_status
        params["qupyt_version"] = qupyt_version
        if return_status == "success":
            data_container.save(
                params["filename"], average_count=int(params["averages"])
            )
        else:
            data_container.save(params["filename"])
        with open(params["filename"] + ".yaml", "w", encoding="utf-8") as file:
            yaml.dump(params, file)
        del data_container
        gc.collect()
    return return_status
