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
from qupyt.measurement_logic.data_handling import (
    DataConfig,
    MeasurementData,
    MeasurementShape,
)

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
    averages = int(params["averages"])
    synchroniser_params = params.get("synchroniser", {})
    upload_to_synchroniser = _as_bool(synchroniser_params.get("upload", True))
    has_dynamic_devices = bool(dynamic_devices.devices)
    mid = datetime.today().strftime("%Y-%m-%d-%H-%M-%S")
    params["filename"] = params["experiment_type"] + "_" + mid
    data: MeasurementData | None = None
    return_status = "all_fail"
    try:
        config = DataConfig.model_validate(params["data"])
        __check_average_is_divisible_by_chunks(
            save_in_chunks=config.save_in_chunks, averages=averages
        )
        shape = MeasurementShape.from_params(params, sensor, config)
        data = MeasurementData(shape, config)
        chunk = MeasurementData(shape, config) if config.save_in_chunks else None
        chunk_index = 0

        for ps_step in tqdm(range(shape.ps_steps)):
            synchroniser.open()
            synchroniser.stop()
            if upload_to_synchroniser:
                synchroniser.load_sequence(
                    get_settings().sequence_dir / f"sequence_{ps_step}.yaml"
                )
            else:
                logger.info(
                    "Skipped synchroniser sequence upload".ljust(65, ".") + "[done]"
                )
            synchroniser.run()
            sleep(0.1)
            sensor.open()
            sleep(0.1)
            for dynamic_step in tqdm(
                range(shape.dynamic_steps), leave=(ps_step == shape.ps_steps - 1)
            ):
                dynamic_devices.next_dynamic_step()
                if has_dynamic_devices:
                    sleep(0.1)
                for avg in tqdm(
                    range(averages),
                    leave=(dynamic_step == shape.dynamic_steps - 1)
                    and (ps_step == shape.ps_steps - 1),
                ):
                    sleep(float(params.get("sleep", 0)))
                    frames = sensor.acquire_data(synchroniser)
                    data.add(frames, ps_step=ps_step, dynamic_step=dynamic_step)
                    if chunk is not None:
                        chunk.add(frames, ps_step=ps_step, dynamic_step=dynamic_step)
                        if (avg + 1) % config.save_in_chunks == 0:
                            chunk.save(
                                f"{params['experiment_type']}_ch-{chunk_index}_{mid}"
                            )
                            chunk.reset()
                            chunk_index += 1
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
        if data is not None:
            data.save(params["filename"])
            params["completed_averages"] = data.averages.tolist()
        with open(params["filename"] + ".yaml", "w", encoding="utf-8") as file:
            yaml.dump(params, file)
        del data
        gc.collect()
    return return_status
