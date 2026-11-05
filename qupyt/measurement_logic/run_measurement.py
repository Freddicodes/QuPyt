"""Main measurement loop."""

from __future__ import annotations

import gc
import logging
from datetime import datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Literal, cast

from tqdm import tqdm

from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.measurement_logic.data_handling import Data, save_measurement_results
from qupyt.set_up import get_output_dir, get_seq_dir
from qupyt.utils.errors import record_failure

logger = logging.getLogger(__name__)

MeasurementStatus = Literal["success", "failed"]

# Pause after synchroniser.run() before the sensor starts acquiring
# (raised from 0.1 s; the AWG was not always ready in time).
RUN_SETTLE_TIME_S = 2.0


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
    # YAML output_dir, else --out-dir / QUPYT_OUT_DIR / current directory.
    out_dir = Path(params.get("output_dir") or get_output_dir()).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _close_hardware(
    sensor: Sensor,
    synchroniser: Synchroniser,
    measurement_id: str,
    had_error: bool = False,
    params: dict[str, Any] | None = None,
) -> bool:
    """Close sensor and synchroniser; one failing close does not skip the other.

    Parameters
    ----------
    sensor : Sensor
        The measurement's sensor.
    synchroniser : Synchroniser
        The measurement's synchroniser.
    measurement_id : str
        Name used in log messages.
    had_error : bool, optional
        True if the measurement itself already failed (only changes the log text).
    params : dict[str, Any] or None, optional
        Measurement parameters; close failures are added to ``cleanup_errors``
        (and to ``measurement_error`` if none was recorded yet).

    Returns
    -------
    bool
        True if both closed without an error.
    """
    closed_successfully = True
    for resource_name, resource in (("sensor", sensor), ("synchroniser", synchroniser)):
        try:
            logger.debug("Closing %s %s", resource_name, type(resource).__name__)
            resource.close()
        except (Exception, SystemExit) as error:
            closed_successfully = False
            if params is not None:
                detail = record_failure(params, error, f"{resource_name} close")
                params.setdefault("cleanup_errors", []).append(detail)
            logger.exception(
                "%s%s: failed to close %s",
                (
                    "Additional cleanup error after measurement failure: "
                    if had_error
                    else ""
                ),
                measurement_id,
                resource_name,
            )
    return closed_successfully


def run_measurement(
    static_devices: DeviceHandler,
    dynamic_devices: DynamicDeviceHandler,
    sensor: Sensor,
    synchroniser: Synchroniser,
    params: dict[str, Any],
) -> MeasurementStatus:
    """Run one measurement and save its data and parameters.

    Steps: apply the static device settings; open the synchroniser once; for
    every pulse sequence step stop the synchroniser, upload
    ``sequence_<n>.yaml`` (unless ``synchroniser.upload`` is false) and run it
    (the sensor is opened once, after the first run); for every dynamic step
    apply the next dynamic device values; for every average acquire one data
    set from the sensor and add it to the data.

    The sensor and synchroniser are always closed at the end, each even if
    the other fails. The data is saved as
    ``<output_dir>/<experiment_type>_<timestamp>.npy`` (divided by
    ``averages`` on success, the raw sum after a failure) and ``params``,
    including ``measurement_status``, ``qupyt_version``, the resolved
    ``output_dir`` and, on failure, ``measurement_error`` (type, message,
    stage and zero-based loop indices) and ``cleanup_errors``, next to it as
    ``.yaml``.

    Used keys of ``params``: ``experiment_type``, ``data`` (see
    :class:`~qupyt.measurement_logic.data_handling.Data`), ``averages``,
    ``dynamic_steps`` (default 1), ``pulse_sequence_steps`` (default 1),
    ``sleep`` (seconds before each acquisition, default 0),
    ``synchroniser.upload`` (default true), ``output_dir`` (optional).

    Parameters
    ----------
    static_devices : DeviceHandler
        Devices with fixed settings.
    dynamic_devices : DynamicDeviceHandler
        Devices swept over the dynamic steps.
    sensor : Sensor
        The sensor to acquire data with.
    synchroniser : Synchroniser
        The synchroniser playing the pulse sequence.
    params : dict[str, Any]
        Measurement parameters from the instruction YAML; updated in place
        (``filename``, ``output_dir``, ``measurement_status``, ...).

    Returns
    -------
    str
        ``"success"`` or ``"failed"``. Errors during the measurement are
        logged and recorded, not raised; ``KeyboardInterrupt`` and
        ``SystemExit`` are recorded and re-raised after saving.
    """
    started = perf_counter()
    mid = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return_status: MeasurementStatus = "failed"
    data_container: Data | None = None
    filename: str | None = None
    out_base: Path | None = None
    averages = 0
    stage = "initialization"
    ps_itervalue = itervalue = avg = None
    acquisition_completed = False
    try:
        params.pop("measurement_error", None)
        params.pop("cleanup_errors", None)
        filename = params["experiment_type"] + "_" + mid
        params["filename"] = filename
        out_dir = _setup_output_dir(params)
        params["output_dir"] = str(out_dir)
        out_base = out_dir / filename
        iterator_size = int(params.get("dynamic_steps", 1))
        ps_iterator_size = int(params.get("pulse_sequence_steps", 1))
        averages = int(params["averages"])
        if min(iterator_size, ps_iterator_size, averages) < 1:
            raise ValueError(
                "dynamic_steps, pulse_sequence_steps, and averages must be positive"
            )
        synchroniser_params = params.get("synchroniser", {})
        upload_to_synchroniser = _as_bool(synchroniser_params.get("upload", True))
        has_dynamic_devices = bool(dynamic_devices.devices)
        logger.info(
            "MEASUREMENT START: %s (pulse_sequences=%s, dynamic_steps=%s, averages=%s)",
            filename,
            ps_iterator_size,
            iterator_size,
            averages,
        )
        stage = "static device configuration"
        static_devices.set_all_params()
        stage = "data allocation"
        data_container = Data(params["data"])
        data_container.set_dims_from_sensor(sensor)
        data_container.create_array()
        data_container.set_filename((params["experiment_type"], mid), out_dir)

        # Synchroniser and sensor are opened once for the whole measurement
        # (opening per pulse sequence step leaked one nidaqmx task per step on
        # the DAQ). As in the original order, the sensor is opened only after
        # the first sequence is uploaded and running.
        stage = "synchroniser open"
        synchroniser.open()
        sensor_opened = False
        for ps_itervalue in tqdm(range(ps_iterator_size)):
            itervalue = avg = None
            logger.info(
                "%s: starting pulse sequence %s/%s",
                filename,
                ps_itervalue + 1,
                ps_iterator_size,
            )
            stage = "synchroniser stop"
            synchroniser.stop()
            if upload_to_synchroniser:
                stage = "sequence upload"
                synchroniser.load_sequence(
                    get_seq_dir() / f"sequence_{ps_itervalue}.yaml"
                )
            else:
                logger.info(
                    "Skipped synchroniser sequence upload".ljust(65, ".") + "[done]"
                )
            stage = "synchroniser run"
            synchroniser.run()
            sleep(RUN_SETTLE_TIME_S)
            if not sensor_opened:
                stage = "sensor open"
                sensor.open()
                sensor_opened = True
            for itervalue in tqdm(
                range(iterator_size), leave=(ps_itervalue == ps_iterator_size - 1)
            ):
                avg = None
                stage = "dynamic device configuration"
                dynamic_devices.next_dynamic_step()
                if has_dynamic_devices:
                    sleep(0.1)
                for avg in tqdm(
                    range(averages),
                    leave=(itervalue == (iterator_size - 1))
                    and (ps_itervalue == (ps_iterator_size - 1)),
                ):
                    sleep(float(params.get("sleep", 0)))
                    stage = "sensor acquisition"
                    data = sensor.acquire_data(synchroniser)
                    stage = "data accumulation"
                    data_container.update_data(data, ps_itervalue, itervalue, avg)
                logger.info(
                    "%s: dynamic step %s/%s done (%s average%s)",
                    filename,
                    itervalue + 1,
                    iterator_size,
                    averages,
                    "s" if averages != 1 else "",
                )
            dynamic_devices.current_dynamic_step = 0
            logger.info(
                "%s: pulse sequence %s/%s done",
                filename,
                ps_itervalue + 1,
                ps_iterator_size,
            )
        acquisition_completed = True
        return_status = "success"
    except Exception as error:
        record_failure(
            params,
            error,
            stage,
            pulse_sequence=ps_itervalue,
            dynamic_step=itervalue,
            average=avg,
        )
        logger.exception(
            "%s failed during %s (zero-based pulse_sequence=%s, dynamic_step=%s, "
            "average=%s)",
            filename or mid,
            stage,
            ps_itervalue,
            itervalue,
            avg,
        )
    except (KeyboardInterrupt, SystemExit) as error:
        record_failure(params, error, stage)
        logger.warning("%s interrupted during %s", filename or mid, stage)
        raise
    finally:
        if not _close_hardware(
            sensor,
            synchroniser,
            filename or mid,
            had_error=not acquisition_completed,
            params=params,
        ):
            return_status = "failed"
        params["measurement_status"] = return_status
        return_status = cast(
            MeasurementStatus,
            save_measurement_results(
                data_container,
                params,
                out_base,
                average_count=averages if acquisition_completed else None,
            ),
        )
        del data_container
        gc.collect()
        log = logger.info if return_status == "success" else logger.error
        log(
            "%s: finished with status %s after %.3fs",
            filename or mid,
            return_status,
            perf_counter() - started,
        )
    return return_status
