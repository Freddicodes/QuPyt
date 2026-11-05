import logging
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from numpy.typing import DTypeLike

from qupyt._version import __version__ as qupyt_version
from qupyt.hardware.sensor import Sensor
from qupyt.mixins import ConfigurationMixin, UpdateConfigurationType
from qupyt.utils.errors import record_failure

logger = logging.getLogger(__name__)


class Data(ConfigurationMixin):
    """Container for the measurement data of one measurement.

    The array has the shape
    ``(reference_channels, ps_steps, dynamic_steps, M, *roi_shape)``:
    the sensor's measurements are split round-robin over the reference
    channels (measurement ``k`` belongs to channel ``k % reference_channels``),
    and every average is **added** (see :meth:`update_data`). ``M`` is 1 in
    ``"sum"`` mode and ``number_measurements / reference_channels`` in
    ``"spread"`` mode.

    Call order: :meth:`set_dims_from_sensor`, :meth:`create_array`, then
    :meth:`update_data` per acquisition and finally :meth:`save`.

    Parameters
    ----------
    configuration : dict
        Configuration keys:
        - ``dynamic_steps`` (int, >= 1, required): number of dynamic steps.
        - ``averaging_mode`` (``"sum"`` or ``"spread"``, required):
        ``"sum"`` adds all measurements of a channel, ``"spread"`` keeps
        them separately.
        - ``ps_steps`` (int, default 1): number of pulse sequence steps.
        - ``reference_channels`` (int, default 2).
        - ``live_compression`` (bool, default False): average over the ROI
        on the fly, so only one value per measurement is kept.
        - ``compress``: deprecated alias of ``live_compression``.
        - ``save_in_chunks`` (int, default 0): additionally save the data
        every N averages to a separate chunk file (0: off).
        - ``number_measurements``, ``roi_shape``: normally taken from the
        sensor by :meth:`set_dims_from_sensor`.
    """

    attribute_map: UpdateConfigurationType

    def __init__(self, configuration: dict[str, Any]) -> None:
        self.roi_shape: list[int]
        self.averaging_mode: str
        self.number_dynamic_steps: int
        self.number_measurements: int
        self.data_type: DTypeLike
        self.live_compression: bool = False
        self.number_pulse_sequences: int = 1
        self.save_in_chunks: int = 0
        self.reference_channels: int = 2
        self.data: np.ndarray
        self.chunk_data: np.ndarray
        self.chunk_save_index: int = 0
        self.filename: tuple[str, str]
        self.out_dir: Path = Path(".")
        self.attribute_map = {
            "dynamic_steps": self._set_number_dynamic_steps,
            "ps_steps": self._set_number_pulse_sequences,
            "averaging_mode": self._set_averaging_mode,
            "number_measurements": self._set_number_measurements,
            "roi_shape": self._set_roi_shape,
            "compress": self._set_compress_mode,
            "live_compression": self._set_live_compression,
            "save_in_chunks": self._set_save_chunk_size,
            "reference_channels": self._set_reference_channels,
        }
        self._update_from_configuration(configuration)

    def _set_save_chunk_size(self, save_in_chunks: int) -> None:
        self.save_in_chunks = save_in_chunks

    def _set_reference_channels(self, reference_channels: int) -> None:
        self.reference_channels = int(reference_channels)

    def _set_compress_mode(self, compression_value: bool) -> None:
        logger.warning(
            "The parameter 'compress' is deprecated; " "use 'live_compression' instead"
        )
        self.live_compression = compression_value

    def _set_live_compression(self, live_compression: bool) -> None:
        self.live_compression = live_compression

    def _set_dtype_from_sensor(self, sensor: Sensor) -> None:
        self.data_type = sensor.target_data_type

    def _set_number_dynamic_steps(self, number_dynamic_steps: int) -> None:
        if number_dynamic_steps < 1:
            raise ValueError(
                """Cannot have fewer than 1 dynamic steps. This will result in an empty measurement.
            If you measure just one step, this is still ONE dynamic step. The first of one."""
            )
        self.number_dynamic_steps = int(number_dynamic_steps)

    def _set_number_pulse_sequences(self, number_pulse_sequences: int) -> None:
        self.number_pulse_sequences = int(number_pulse_sequences)

    def _set_averaging_mode(self, averaging_mode: str) -> None:
        self.averaging_mode = str(averaging_mode)

    def _set_number_measurements(self, number_measurements: int) -> None:
        self.number_measurements = number_measurements

    def _set_roi_shape(self, roi_shape: list[int]) -> None:
        self.roi_shape = roi_shape

    def _set_filename(self, filename: tuple[str, str]) -> None:
        self.filename = filename

    def set_filename(
        self, filename: tuple[str, str], out_dir: Path | str = "."
    ) -> None:
        """Set the name parts and directory used for chunk files.

        Parameters
        ----------
        filename : tuple[str, str]
            ``(prefix, timestamp)`` used to build chunk file names.
        out_dir : Path | str
            Directory the chunk files are written to.
        """
        self._set_filename(filename)
        self.out_dir = Path(out_dir)

    def set_dims_from_sensor(self, sensor: Sensor) -> None:
        """Take ``roi_shape``, ``number_measurements`` and (if the sensor has one) ``target_data_type`` from the sensor."""
        self._set_ROI_from_sensor(sensor)
        self._set_number_measurements_from_sensor(sensor)
        if hasattr(sensor, "target_data_type"):
            self._set_dtype_from_sensor(sensor)

    def _set_ROI_from_sensor(self, sensor: Sensor) -> None:
        self.roi_shape = sensor.roi_shape

    def _set_number_measurements_from_sensor(self, sensor: Sensor) -> None:
        self.number_measurements = sensor.number_measurements

    def create_array(self) -> None:
        """Allocate ``self.data`` (and ``self.chunk_data``) filled with zeros, shape as described in the class docstring.

        With ``live_compression`` the ROI is reduced to one value and the dtype
        becomes ``float``.

        Raises
        ------
        ValueError
            for an unknown ``averaging_mode``, or in
            ``"spread"`` mode if ``number_measurements`` is not divisible by
            ``reference_channels``.
        """
        # HeliCam gets number_images, whereas other get number_images / 2.
        # I suggest fixing this as number_images
        # Specifics have then to be dealt with in each class.
        if (
            min(
                self.reference_channels,
                self.number_pulse_sequences,
                self.number_dynamic_steps,
                self.number_measurements,
            )
            < 1
        ):
            raise ValueError("Data dimensions and reference_channels must be positive")
        if self.save_in_chunks < 0:
            raise ValueError("save_in_chunks must be zero or a positive chunk size")
        # Shape the sensor delivers (before live compression reduces the ROI).
        self._input_roi_shape = tuple(self.roi_shape)
        if self.live_compression:
            self.roi_shape = [1]
            self.data_type = float
        if self.averaging_mode == "sum":
            data_array_dim = [
                self.reference_channels,
                self.number_pulse_sequences,
                self.number_dynamic_steps,
                1,
                *self.roi_shape,
            ]
        elif self.averaging_mode == "spread":
            measurements_per_channel = (
                self.number_measurements / self.reference_channels
            )
            if measurements_per_channel != int(measurements_per_channel):
                raise ValueError(
                    f"number_measurements ({self.number_measurements}) must be "
                    f"divisible by reference_channels ({self.reference_channels}) "
                    "in averaging_mode 'spread'"
                )
            data_array_dim = [
                self.reference_channels,
                self.number_pulse_sequences,
                self.number_dynamic_steps,
                int(measurements_per_channel),
                *self.roi_shape,
            ]
        else:
            raise ValueError(
                f"averaging_mode {self.averaging_mode!r} not available; "
                "use 'sum' or 'spread'"
            )
        self.data = np.zeros(data_array_dim, dtype=getattr(self, "data_type", float))
        self.chunk_data = np.zeros_like(self.data)
        logger.info(
            f"Created data array of shape {list(self.data.shape)}".ljust(65, ".")
            + "[done]"
        )
        logger.debug(
            "Created data arrays: shape=%s dtype=%s averaging_mode=%s "
            "live_compression=%s chunk_size=%s allocated_bytes=%d",
            self.data.shape,
            self.data.dtype,
            self.averaging_mode,
            self.live_compression,
            self.save_in_chunks,
            self.data.nbytes + self.chunk_data.nbytes,
        )

    def update_data(
        self, data: np.ndarray, ps_step: int, dynamic_step: int, avg_step: int
    ) -> None:
        """Add one acquisition to the data (and the chunk data, if enabled).

        Every ``save_in_chunks`` averages the chunk is saved, divided by the
        chunk size, and reset.

        Parameters
        ----------
        data : np.ndarray
            Sensor output, first axis = measurements.
        ps_step : int
            Index of the pulse sequence step.
        dynamic_step : int
            Index of the dynamic step.
        avg_step : int
            Index of the current average (counts from 0).
        """
        expected_shape = (self.number_measurements, *self._input_roi_shape)
        if data.shape != expected_shape:
            raise ValueError(
                f"Sensor returned data shape {data.shape}; expected {expected_shape}"
            )
        if self.live_compression:
            self._update_data_compressed(data, ps_step, dynamic_step)
            if self.save_in_chunks != 0:
                self._update_data_compressed(
                    data, ps_step, dynamic_step, target_array=self.chunk_data
                )
        else:
            self._update_data_full(data, ps_step, dynamic_step)
            if self.save_in_chunks != 0:
                self._update_data_full(
                    data, ps_step, dynamic_step, target_array=self.chunk_data
                )
        if self.save_in_chunks != 0 and (avg_step + 1) % self.save_in_chunks == 0:
            self.save(
                self.out_dir
                / f"{self.filename[0]}_ch-{self.chunk_save_index}_{self.filename[1]}",
                source_data=self.chunk_data,
                average_count=self.save_in_chunks,
            )
            self.chunk_save_index += 1
            self.chunk_data.fill(0)
        logger.debug(
            "Accumulated data: pulse_sequence=%s, dynamic_step=%s, average=%s",
            ps_step + 1,
            dynamic_step + 1,
            avg_step + 1,
        )

    def _update_data_full(
        self,
        data: np.ndarray,
        ps_step: int,
        dynamic_step: int,
        target_array: np.ndarray | None = None,
    ) -> None:
        if target_array is None:
            target_array = self.data
        if self.averaging_mode == "sum":
            for i in range(self.reference_channels):
                target_array[i, ps_step, dynamic_step] += data[
                    i :: self.reference_channels
                ].sum(axis=0)
            # np.save("C:/Users/ge54vec/.qupyt/data", self.data)
        elif self.averaging_mode == "spread":
            for i in range(self.reference_channels):
                target_array[i, ps_step, dynamic_step] += data[
                    i :: self.reference_channels
                ]
                # np.save("C:/Users/ge54vec/.qupyt/data", self.data)

    def _update_data_compressed(
        self,
        data: np.ndarray,
        ps_step: int,
        dynamic_step: int,
        target_array: np.ndarray | None = None,
    ) -> None:
        if target_array is None:
            target_array = self.data
        if self.averaging_mode == "sum":
            for i in range(self.reference_channels):
                ndim = data.ndim
                target_array[i, ps_step, dynamic_step] += (
                    data[i :: self.reference_channels]
                    .mean(axis=tuple(range(1, ndim)))
                    .sum(axis=0)
                )
        elif self.averaging_mode == "spread":
            for i in range(self.reference_channels):
                ndim = data.ndim
                target_array[i, ps_step, dynamic_step] += (
                    data[i :: self.reference_channels]
                    .mean(axis=tuple(range(1, ndim)))
                    .reshape(-1, 1)
                )

    def save(
        self,
        filename: str | Path,
        source_data: np.ndarray | None = None,
        average_count: int | None = None,
    ) -> None:
        """Save data (without metadata) to a .npy file.

        Parameters
        ----------
        filename : str | Path
            Name of the resulting data file.
            During normal usage as part of QuPyt, this will be assigned by the
            main measurement loop.
        source_data : np.ndarray | None
            Array to save instead of ``self.data``.
        average_count : int | None
            If > 0, the data is divided by it before saving
            (``self.data`` sums the averages). ``None`` or 0 saves the raw sum.
        """
        if source_data is None:
            source_data = self.data
        output_path = str(Path(filename).absolute())
        if not output_path.endswith(".npy"):
            output_path += ".npy"
        logger.debug("Saving data to %s", output_path)
        try:
            if average_count is not None and average_count > 0:
                np.save(filename, source_data / average_count)
            else:
                np.save(filename, source_data)
        except Exception:
            logger.exception("Failed to save data to %s", output_path)
            raise
        logger.info(
            "Saved data to %s (shape=%s, average_count=%s)",
            output_path,
            source_data.shape,
            average_count,
        )


def save_measurement_results(
    data_container: Data | None,
    params: dict[str, Any],
    out_base: Path | None,
    average_count: int | None = None,
) -> str:
    """Save the data and the parameters; record save failures in the status.

    Parameters
    ----------
    data_container : Data or None
        The measurement data, or None if allocation never happened.
    params : dict[str, Any]
        Measurement parameters, written to ``<out_base>.yaml``. A failed save
        sets ``measurement_status`` to ``"failed"`` and adds
        ``measurement_error`` (if none was recorded yet).
    out_base : pathlib.Path or None
        Output path without extension; None if the measurement failed before
        a file name was assigned (nothing is written then).
    average_count : int or None, optional
        Number of averages to divide by; None saves the raw sum (used after a
        failure, when not all averages were acquired).

    Returns
    -------
    str
        The final ``measurement_status``.
    """
    params["qupyt_version"] = qupyt_version
    if (
        out_base is not None
        and data_container is not None
        and hasattr(data_container, "data")
    ):
        try:
            if average_count is None:
                logger.warning(
                    "Saving partial, unaveraged data for failed measurement %s",
                    out_base.name,
                )
            data_container.save(out_base, average_count=average_count)
        except Exception as error:
            params["measurement_status"] = "failed"
            record_failure(params, error, "data saving")
            logger.exception("%s: failed to save data", out_base.name)
    else:
        logger.warning(
            "%s has no allocated data to save",
            out_base.name if out_base is not None else params.get("experiment_type"),
        )
    if out_base is not None:
        yaml_path = out_base.with_name(out_base.name + ".yaml")
        try:
            with open(yaml_path, "w", encoding="utf-8") as file:
                yaml.dump(params, file)
            logger.info("Saved measurement metadata to %s", yaml_path)
        except Exception as error:
            params["measurement_status"] = "failed"
            record_failure(params, error, "metadata saving")
            logger.exception("%s: failed to save metadata", out_base.name)
    return str(params["measurement_status"])
