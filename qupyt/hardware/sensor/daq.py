from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from qupyt.mixins import DAQOpenError
from qupyt.utils.decorators import log_sensor_acquisition
from qupyt.utils.errors import cleanup_after_error, cleanup_on_exit

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser import Synchroniser

logger = logging.getLogger(__name__)


class DAQ(Sensor, sensor_types=("DAQ",)):
    """Interface to Data Acquisition Units from National Instruments.

    It specifically focuses on devices that use the `nidaqmx
    <https://github.com/ni/nidaqmx-python/>`_ library.

    Parameters
    ----------
    configuration : dict
        Configuration dictionary. Keys will be used
        to select setter methods from an attribute map dictionary to set
        associated values.

        Possible configuration values:
          - **number_measurements** (int): Number of samples read per call
            to :meth:`acquire_data`. The task runs in finite acquisition
            mode and takes one sample per rising edge of the sample clock,
            so this must match the number of clock pulses the synchroniser
            sends per sequence. :meth:`acquire_data` returns an array of
            shape ``[number_measurements, 1]`` (``roi_shape`` is ``[1]``).
            The value is applied when :meth:`open` configures the task.
          - **min_voltage** (float, V): Lower limit of the analog input
            range (default -1.0). Consult your device manual for the
            supported ranges.
          - **max_voltage** (float, V): Upper limit of the analog input
            range (default 1.0).
          - **apd_input** (str): Physical analog input channel, e.g. the
            default "Dev3/ai0". "Dev3" is the device name assigned by NI-DAQmx
            when the device is connected (look it up in NI MAX); "ai0" is
            the analog input channel. The channel is read in RSE
            (referenced single-ended) mode.
          - **sample_clk** (str): Terminal of the external sample clock,
            e.g. the default "PFI1" (PFI: Programmable Function Interface).
            It is also used as the AI convert clock.
          - **start_trig** (str): Terminal of the digital start trigger,
            e.g. the default "PFI0". Acquisition starts on its rising edge.
          - **max_samp_rate** (float, Hz): Expected maximum rate of the
            external sample clock (default 625e3). NI-DAQmx uses it to
            configure the task timing; it does not set the actual rate.

        Note that these configuration attributes extend those from the
        :class:`Sensor` base class. The read timeout is fixed at 10 s.
    """

    def __init__(self, configuration: dict[str, Any] | None) -> None:

        try:
            import nidaqmx
            import nidaqmx.constants
        except ImportError as e:
            raise ImportError(
                "DAQ needs package 'nidaqmx'. Install with pip install qupyt[daq]"
            ) from e

        self._nidaqmx = nidaqmx
        # The enums (TerminalConfiguration, Edge, ...) live in nidaqmx.constants.
        self._c = nidaqmx.constants
        # configure various input ports ->
        # see inside NI-DAQ hardware for assignment.
        self.daq_apd_input: str = "Dev3/ai0"
        self.daq_sample_clk: str = "PFI1"
        self.daq_start_trig: str = "PFI0"
        self.daq_max_sampling_rate: float = 625e3
        # AI (analog input) in volts
        self.min_voltage: float = -1.0
        self.max_voltage: float = 1.0
        self.daq_timeout: int = 10  # / s
        super().__init__(configuration)
        self.roi_shape = [1]
        self.attribute_map["min_voltage"] = self._set_min_voltage
        self.attribute_map["max_voltage"] = self._set_max_voltage
        self.attribute_map["apd_input"] = self._set_apd_input
        self.attribute_map["sample_clk"] = self._set_sample_clk
        self.attribute_map["start_trig"] = self._set_start_trig
        self.attribute_map["max_samp_rate"] = self._set_max_sampling_rate
        self.initial_configuration_dict = configuration
        if configuration is not None:
            self._update_from_configuration(configuration)
        # nidaqmx.Task after open(); typed Any because nidaqmx is untyped here.
        self.daq_task: Any = None

    def __repr__(self) -> str:
        return f"DAQ(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"NI-DAQ sensor instance (nidaqmx): DAQ(configuration: {self.initial_configuration_dict})"

    def open(self) -> None:
        """Configure an analog input voltage channel, sample clock and start trigger."""
        if self.daq_task is not None:
            # task is already open
            raise RuntimeError("DAQ task is already open; call close() first")
        try:
            self.daq_task = self._nidaqmx.Task()
            self._create_analog_input_channel()
            self._configure_sample_clock()
            self._configure_analog_input_trigger()
            logger.info("DAQ opened and created read task".ljust(65, "."))
        except Exception as e:
            logger.exception(
                "Failed to open or configure DAQ input %s", self.daq_apd_input
            )
            cleanup_after_error(self.close, "DAQ setup cleanup")
            raise DAQOpenError(
                "An error occurred while opening and configuring the DAQ. Please "
                f"make sure the DAQ is connected and powered on. Cause: {e}"
            ) from e

    def _create_analog_input_channel(self) -> None:
        # create analog channel to measure voltage
        _ = self.daq_task.ai_channels.add_ai_voltage_chan(
            self.daq_apd_input,
            "",
            self._c.TerminalConfiguration.RSE,
            self.min_voltage,
            self.max_voltage,
            self._c.VoltageUnits.VOLTS,
        )

    def _configure_analog_input_trigger(self) -> None:
        # Configure convert clock
        # Specifies the terminal of the signal to use
        # as the AI Convert Clock.
        self.daq_task.timing.ai_conv_src = self.daq_sample_clk
        self.daq_task.timing.ai_conv_active_edge = self._c.Edge.RISING
        read_start_trig = self.daq_task.triggers.start_trigger
        # Configures the task to start acquiring samples
        # on the active edge of a digital signal.
        read_start_trig.cfg_dig_edge_start_trig(
            self.daq_start_trig, self._c.Edge.RISING
        )

    def _configure_sample_clock(self) -> None:
        # Configure sample clock : Sets the clock source, the clock rate,
        # active clock edge, sample mode and the number of
        # samples to acquire.
        self.daq_task.timing.cfg_samp_clk_timing(
            self.daq_max_sampling_rate,
            self.daq_sample_clk,
            self._c.Edge.RISING,
            self._c.AcquisitionType.FINITE,
            int(self.number_measurements),
        )

    @log_sensor_acquisition
    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """Read all samples at once from the configured sensor.

        See :meth:`Sensor.acquire_data`.
        """
        if self.daq_task is None:
            raise RuntimeError("DAQ must be opened before acquiring data")
        samples_to_read = int(self.number_measurements)
        stage = "task start"
        try:
            # The task is stopped even if triggering or reading fails.
            with cleanup_on_exit(self.daq_task.stop, "DAQ acquisition stop"):
                if synchroniser is not None:
                    self.daq_task.start()
                    stage = "trigger"
                    synchroniser.trigger()
                stage = "read"
                samples = self.daq_task.read(samples_to_read, self.daq_timeout)
                stage = "task stop"
        except Exception:
            logger.exception(
                "DAQ acquisition failed during %s: input=%s, samples=%s, timeout=%ss",
                stage,
                self.daq_apd_input,
                samples_to_read,
                self.daq_timeout,
            )
            raise
        logger.debug("DAQ acquisition completed: %d samples", len(samples))
        return np.asarray(samples).reshape((len(samples), 1))

    def _set_min_voltage(self, min_voltage: float) -> None:
        self.min_voltage = float(min_voltage)

    def _set_max_voltage(self, max_voltage: float) -> None:
        self.max_voltage = float(max_voltage)

    def _set_start_trig(self, start_trig: str) -> None:
        self.daq_start_trig = start_trig

    def _set_max_sampling_rate(self, max_sampling_rate: float) -> None:
        self.daq_max_sampling_rate = max_sampling_rate

    def _set_apd_input(self, apd_input: str) -> None:
        self.daq_apd_input = apd_input

    def _set_sample_clk(self, sample_clk: str) -> None:
        self.daq_sample_clk = sample_clk

    def close(self) -> None:
        """See :meth:`Sensor.close`."""
        if self.daq_task is None:
            return
        # Keep the task if closing fails, so close() can be retried.
        self.daq_task.close()
        self.daq_task = None
        logger.info("DAQ closed".ljust(65, "."))
