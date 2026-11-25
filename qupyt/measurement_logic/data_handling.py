"""
Measurement data container. Axes: ``(channel, ps_step, dynamic_step, measurement, *roi)``.
See ``source/measurement_flow.rst`` for details.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from os import PathLike
from typing import Any, Self

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt

from qupyt.hardware.sensor import Sensor

logger = logging.getLogger(__name__)


class AveragingMode(StrEnum):
    SUM = "sum"
    SPREAD = "spread"


class DataConfig(BaseModel):
    """The ``data:`` block of a measurement YAML file."""

    model_config = ConfigDict(extra="forbid")

    averaging_mode: AveragingMode
    reference_channels: PositiveInt = 2
    live_compression: bool = False
    save_in_chunks: NonNegativeInt = 0


@dataclass(frozen=True)
class MeasurementShape:
    """Sizes of every data axis. The measurement loops iterate over these."""

    channels: int
    ps_steps: int
    dynamic_steps: int
    measurements_per_channel: int
    roi: tuple[int, ...]

    @classmethod
    def from_params(
        cls, params: dict[str, Any], sensor: Sensor, config: DataConfig
    ) -> Self:
        ps_steps = int(params.get("pulse_sequence_steps", 1))
        dynamic_steps = int(params.get("dynamic_steps", 1))
        if ps_steps < 1 or dynamic_steps < 1:
            raise ValueError(
                f"pulse_sequence_steps ({ps_steps}) and dynamic_steps ({dynamic_steps}) "
                "must be at least 1."
            )

        channels = config.reference_channels
        number_measurements = int(sensor.number_measurements)
        if number_measurements % channels != 0:
            raise ValueError(
                f"number_measurements ({number_measurements}) must be divisible by "
                f"data.reference_channels ({channels})."
            )

        if config.averaging_mode == AveragingMode.SPREAD:
            measurements_per_channel = number_measurements // channels
        else:
            measurements_per_channel = 1
        roi = (
            (1,) if config.live_compression else tuple(int(n) for n in sensor.roi_shape)
        )

        return cls(channels, ps_steps, dynamic_steps, measurements_per_channel, roi)

    @property
    def axes(self) -> tuple[str, ...]:
        roi_axes = tuple(f"roi_{i}" for i in range(len(self.roi)))
        return ("channel", "ps_step", "dynamic_step", "measurement", *roi_axes)

    @property
    def array_shape(self) -> tuple[int, ...]:
        return (
            self.channels,
            self.ps_steps,
            self.dynamic_steps,
            self.measurements_per_channel,
            *self.roi,
        )


class MeasurementData:
    """
    Accumulates sensor readouts into the measurement array.

    Sums are kept in float64 regardless of the sensor dtype, so they cannot
    overflow. Each ``(ps_step, dynamic_step)`` counts its own averages, so
    :meth:`mean` is correct for incomplete (failed) runs as well.
    """

    def __init__(self, shape: MeasurementShape, config: DataConfig) -> None:
        self.shape = shape
        self.config = config
        self._sum = np.zeros(shape.array_shape, dtype=np.float64)
        self.averages = np.zeros((shape.ps_steps, shape.dynamic_steps), dtype=np.int64)
        logger.info(
            f"Created data array of shape {shape.array_shape}".ljust(65, ".") + "[done]"
        )

    def add(self, frames: np.ndarray, *, ps_step: int, dynamic_step: int) -> None:
        """
        Add one sensor readout of shape ``(number_measurements, *roi_shape)``
        as one average of ``(ps_step, dynamic_step)``.
        """
        frames = np.asarray(frames, dtype=np.float64)
        channels = self.shape.channels
        if self.config.live_compression:
            frames = frames.reshape(len(frames), -1).mean(axis=1, keepdims=True)
        if frames.shape[1:] != self.shape.roi or len(frames) % channels:
            raise ValueError(
                f"Sensor returned shape {frames.shape}, expected "
                f"(multiple of {channels}, *{self.shape.roi})."
            )

        # Frame k belongs to channel k % channels -> (channel, frame, *roi).
        per_channel = frames.reshape(-1, channels, *self.shape.roi).swapaxes(0, 1)
        if self.config.averaging_mode == AveragingMode.SUM:
            per_channel = per_channel.sum(axis=1, keepdims=True)

        self._sum[:, ps_step, dynamic_step] += per_channel
        self.averages[ps_step, dynamic_step] += 1

    def mean(self) -> np.ndarray:
        """Sum divided by the averages of each slot. NaN where no average was recorded."""
        averages = self.averages.reshape(
            1, *self.averages.shape, *(1,) * (self._sum.ndim - 3)
        )
        return np.divide(
            self._sum,
            averages,
            out=np.full_like(self._sum, np.nan),
            where=averages > 0,
        )

    def reset(self) -> None:
        self._sum.fill(0)
        self.averages.fill(0)

    def save(self, filename: str | PathLike[str]) -> None:
        """
        Write ``<filename>.npz`` with the arrays ``data`` (see :meth:`mean`),
        ``averages`` (per ``(ps_step, dynamic_step)``) and ``axes`` (axis names).
        """
        np.savez(
            filename,
            data=self.mean(),
            averages=self.averages,
            axes=np.array(self.shape.axes),
        )


@dataclass(frozen=True)
class Measurement:
    """A saved measurement, as returned by :func:`load_measurement`."""

    data: np.ndarray
    averages: np.ndarray
    axes: tuple[str, ...]
    params: dict[str, Any]

    def sel(self, **indices: int | slice) -> np.ndarray:
        """
        Index the data by axis name, e.g. ``m.sel(channel=0, dynamic_step=3)``.
        Axes not named are kept whole.
        """
        unknown = set(indices) - set(self.axes)
        if unknown:
            raise KeyError(f"Unknown axes {sorted(unknown)}; available: {self.axes}")
        return self.data[tuple(indices.get(axis, slice(None)) for axis in self.axes)]


def load_measurement(filename: str | PathLike[str]) -> Measurement:
    """
    Load ``<filename>.npz`` and its ``<filename>.yaml``. The file extension
    may be given or left out.
    """
    base = str(filename).removesuffix(".npz").removesuffix(".yaml")
    with np.load(base + ".npz") as npz:
        data, averages, axes = npz["data"], npz["averages"], tuple(npz["axes"].tolist())
    with open(base + ".yaml", encoding="utf-8") as file:
        params = yaml.safe_load(file)
    return Measurement(data=data, averages=averages, axes=axes, params=params)
