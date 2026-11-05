"""
Main measurement loop.
"""

import gc
import logging
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any, Literal

import yaml
from tqdm import tqdm

from qupyt._version import __version__ as qupyt_version
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.measurement_logic.data_handling import Data
from qupyt.set_up import get_output_dir, get_seq_dir

logger = logging.getLogger(__name__)

# "all_fail" is only the initial value: it stays (and is written to the
# params YAML) when a BaseException such as KeyboardInterrupt escapes.
MeasurementStatus = Literal["success", "failed", "all_fail"]

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

def _setup_output_dir(params: dict[str, Any]) -> Path:
    # either from yaml or env or CLI
    out_dir = Path(params.get("output_dir") or get_output_dir()).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir

def run_measurement(
    static_devices: DeviceHandler,
    dynamic_devices: DynamicDeviceHandler,
    sensor: Sensor,
    synchroniser: Synchroniser,
    params: dict[str, Any],
) -> MeasurementStatus:
    """
    Run one measurement and save its data and parameters.

    Steps: apply the static device settings; for every pulse sequence step
    stop the synchroniser, upload ``sequence_<n>.yaml`` (unless
    ``synchroniser.upload`` is false) and run it; for every dynamic step
    apply the next dynamic device values; for every average acquire one
    data set from the sensor and add it to the data.

    The sensor and synchroniser are always closed at the end. The data is
    saved as ``<output_dir>/<experiment_type>_<timestamp>.npy`` (divided by
    ``averages`` on success, the raw sum after a failure) and ``params``,
    including ``measurement_status``, ``qupyt_version`` and the resolved
    ``output_dir``, next to it as ``.yaml``.

    Used keys of ``params``: ``experiment_type``, ``data`` (see
    :class:`~qupyt.measurement_logic.data_handling.Data`), ``averages``,
    ``dynamic_steps`` (default 1), ``pulse_sequence_steps`` (default 1),
    ``sleep`` (seconds before each acquisition, default 0),
    ``synchroniser.upload`` (default true), ``output_dir`` (optional).

    :param static_devices: Devices with fixed settings.
    :param dynamic_devices: Devices swept over the dynamic steps.
    :param sensor: The sensor to acquire data with.
    :param synchroniser: The synchroniser playing the pulse sequence.
    :param params: Measurement parameters from the instruction YAML; updated
        in place (``filename``, ``output_dir``, ``measurement_status``, ...).
    :return: ``"success"`` or ``"failed"``. Errors during the measurement
        are logged, not raised.
    """
    static_devices.set_all_params()
    iterator_size = int(params.get("dynamic_steps", 1))
    ps_iterator_size = int(params.get("pulse_sequence_steps", 1))
    synchroniser_params = params.get("synchroniser", {})
    upload_to_synchroniser = _as_bool(synchroniser_params.get("upload", True))
    has_dynamic_devices = bool(dynamic_devices.devices)
    mid = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return_status: MeasurementStatus = "all_fail"
    

    data_container = None
    params["filename"] = params["experiment_type"] + "_" + mid
    out_dir = _setup_output_dir(params=params)
    params["output_dir"] = str(out_dir)
    out_base = out_dir / params["filename"]

    try:
        data_container = Data(params["data"])
        data_container.set_dims_from_sensor(sensor)
        data_container.create_array()
        data_container.set_filename((params["experiment_type"], mid), out_dir)

        sensor.open()
        synchroniser.open()
        for ps_itervalue in tqdm(range(ps_iterator_size)):
            synchroniser.stop()
            if upload_to_synchroniser:
                synchroniser.load_sequence(get_seq_dir() / f"sequence_{ps_itervalue}.yaml")
            else:
                logger.info("Skipped synchroniser sequence upload".ljust(65, ".") + "[done]")
            synchroniser.run()
            sleep(0.1)
            for itervalue in tqdm(range(iterator_size), leave=(ps_itervalue == ps_iterator_size - 1)):
                dynamic_devices.next_dynamic_step()
                if has_dynamic_devices:
                    sleep(0.1)
                for avg in tqdm(
                        range(int(params["averages"])),
                        leave=(itervalue == (iterator_size - 1)) and (ps_itervalue == (ps_iterator_size - 1)),
                ):

                    sleep(float(params.get("sleep", 0)))
                    data = sensor.acquire_data(synchroniser)
                    data_container.update_data(data, ps_itervalue, itervalue, avg)
            dynamic_devices.current_dynamic_step = 0
        return_status = "success"
    except Exception:
        logger.exception("An error occurred during the measurement!")
        return_status = "failed"
    finally:
        # one time close at the end
        sensor.close()
        synchroniser.close()

        params["measurement_status"] = return_status
        params["qupyt_version"] = qupyt_version
        if data_container is not None:
            if return_status == "success":
                data_container.save(out_base, average_count=int(params["averages"]))
            else:
                data_container.save(out_base)
        with open(out_base.with_name(out_base.name + ".yaml"), "w", encoding="utf-8") as file:
            yaml.dump(params, file)
        del data_container
        gc.collect()
    return return_status
