import logging
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import DTypeLike

from qupyt.hardware.sensor import Sensor
from qupyt.mixins import ConfigurationMixin, UpdateConfigurationType

logger = logging.getLogger(__name__)

class Data(ConfigurationMixin):
    """
    Container for the measurement data of one measurement.

    The array has the shape
    ``(reference_channels, ps_steps, dynamic_steps, M, *roi_shape)``:
    the sensor's measurements are split round-robin over the reference
    channels (measurement ``k`` belongs to channel ``k % reference_channels``),
    and every average is **added** (see :meth:`update_data`). ``M`` is 1 in
    ``"sum"`` mode and ``number_measurements / reference_channels`` in
    ``"spread"`` mode.

    Call order: :meth:`set_dims_from_sensor`, :meth:`create_array`, then
    :meth:`update_data` per acquisition and finally :meth:`save`.

    :param configuration: Configuration keys:

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
    :type configuration: dict
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
            "The parameter 'compress' is deprecated; "
            "use 'live_compression' instead"
        )
        self.live_compression = compression_value

    def _set_live_compression(self, live_compression: bool) -> None:
        self.live_compression = live_compression

    def _set_dtype_from_sensor(self, sensor: Sensor) -> None:
        self.data_type = sensor.target_data_type

    def _set_number_dynamic_steps(self, number_dynamic_steps: int) -> None:
        if number_dynamic_steps < 1:
            raise ValueError("""Cannot have fewer than 1 dynamic steps. This will result in an empty measurement.
            If you measure just one step, this is still ONE dynamic step. The first of one.""")
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

    def set_filename(self, filename: tuple[str, str], out_dir: Path | str = ".") -> None:
        """
        :param filename: ``(prefix, timestamp)`` used to build chunk file names.
        :param out_dir: Directory the chunk files are written to.
        """
        self._set_filename(filename)
        self.out_dir = Path(out_dir)

    def set_dims_from_sensor(self, sensor: Sensor) -> None:
        """
        Take ``roi_shape``, ``number_measurements`` and (if the sensor has
        one) ``target_data_type`` from the sensor.
        """
        self._set_ROI_from_sensor(sensor)
        self._set_number_measurements_from_sensor(sensor)
        if hasattr(sensor, "target_data_type"):
            self._set_dtype_from_sensor(sensor)

    def _set_ROI_from_sensor(self, sensor: Sensor) -> None:
        self.roi_shape = sensor.roi_shape

    def _set_number_measurements_from_sensor(self, sensor: Sensor) -> None:
        self.number_measurements = sensor.number_measurements

    def create_array(self) -> None:
        """
        Allocate ``self.data`` (and ``self.chunk_data``) filled with zeros,
        shape as described in the class docstring. With ``live_compression``
        the ROI is reduced to one value and the dtype becomes ``float``.

        :raises ValueError: for an unknown ``averaging_mode``, or in
            ``"spread"`` mode if ``number_measurements`` is not divisible by
            ``reference_channels``.
        """
        # HeliCam gets number_images, whereas other get number_images / 2.
        # I suggest fixing this as number_images
        # Specifics have then to be dealt with in each class.
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
            measurements_per_channel = self.number_measurements / self.reference_channels
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
        logger.info(
            f"Created data array of shape {data_array_dim}".ljust(65, ".") + "[done]"
        )
        self.data = np.zeros(data_array_dim, dtype=getattr(self, "data_type", float))
        self.chunk_data = np.zeros_like(self.data)

    def update_data(self, data: np.ndarray, ps_step: int, dynamic_step: int, avg_step: int) -> None:
        """
        Add one acquisition to the data (and the chunk data, if enabled).
        Every ``save_in_chunks`` averages the chunk is saved, divided by the
        chunk size, and reset.

        :param data: Sensor output, first axis = measurements.
        :param ps_step: Index of the pulse sequence step.
        :param dynamic_step: Index of the dynamic step.
        :param avg_step: Index of the current average (counts from 0).
        """
        if self.live_compression:
            self._update_data_compressed(data, ps_step, dynamic_step)
            if self.save_in_chunks != 0:
                self._update_data_compressed(data, ps_step, dynamic_step, target_array=self.chunk_data)
        else:
            self._update_data_full(data, ps_step, dynamic_step)
            if self.save_in_chunks != 0:
                self._update_data_full(data, ps_step, dynamic_step, target_array=self.chunk_data)
        if (
            self.save_in_chunks != 0
            and (avg_step + 1) % self.save_in_chunks == 0
        ):
            self.save(
                self.out_dir / f"{self.filename[0]}_ch-{self.chunk_save_index}_{self.filename[1]}",
                source_data=self.chunk_data,
                average_count=self.save_in_chunks,
            )
            self.chunk_save_index += 1
            self.chunk_data.fill(0)

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
                target_array[i, ps_step, dynamic_step] += data[i :: self.reference_channels].sum(
                    axis=0
                )
            # np.save("C:/Users/ge54vec/.qupyt/data", self.data)
        elif self.averaging_mode == "spread":
            for i in range(self.reference_channels):
                target_array[i, ps_step, dynamic_step] += data[i :: self.reference_channels]
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
                    data[i :: self.reference_channels].mean(axis=tuple(range(1, ndim))).sum(axis=0)
                )
        elif self.averaging_mode == "spread":
            for i in range(self.reference_channels):
                ndim = data.ndim
                target_array[i, ps_step, dynamic_step] += (
                    data[i :: self.reference_channels].mean(axis=tuple(range(1, ndim))).reshape(-1, 1)
                )

    def save(
        self,
        filename: str | Path,
        source_data: np.ndarray | None = None,
        average_count: int | None = None,
    ) -> None:
        """
        Save data (without metadata) to a .npy file.

        :param filename: Name of the resulting data file.
         During normal usage as part of QuPyt, this will be assigned by the
         main measurement loop.
        :type filename: str | Path
        :param source_data: Array to save instead of ``self.data``.
        :type source_data: np.ndarray | None
        :param average_count: If > 0, the data is divided by it before saving
         (``self.data`` sums the averages). ``None`` or 0 saves the raw sum.
        :type average_count: int | None
        """
        if source_data is None:
            source_data = self.data
        if average_count is not None and average_count > 0:
            np.save(filename, source_data / average_count)
            return
        np.save(filename, source_data)
