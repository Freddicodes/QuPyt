import logging
import sys
import traceback
from typing import TYPE_CHECKING, Any

import numpy as np

from qupyt.utils.imports import install_hint

from .base import Sensor

if TYPE_CHECKING:
    from qupyt.hardware.synchroniser.base import Synchroniser

logger = logging.getLogger(__name__)


class DAQ(Sensor, sensor_types=("DAQ",)):
    """
    This class interfaces with Data Acquisition Units from National Instruments.
    It specifically focusses on devices that use the
    `nidaqmx <https://github.com/ni/nidaqmx-python/>`_ library.

    Arguments:
        - **configuration** (dict): Configuration dictionary. Keys will be used
          to select setter methods from an attribute map dicionary to set
          associated values.

          Possible configuration values:
            - **number_measurements** (int, even): HeliCam overwrites
              the base :class:`Sensor` class's
              set_number_measurements logic, because every frame produced
              by the heliCam C3 contains two sub frames. We reshape this nested
              frame structure into a flat array of frames in accordance to all
              other camers. However, this implied that internally, the
              configuration for the number of frames works slighly differently.
            - **min_voltage** (float): Mininmum voltage level for the DAQ to digitise.
              For the specific range of your device please consult your manual.
            - **max_voltage** (float): Maximum voltage level for the DAQ to digitise.
              For the specific range of your device please consult your manual.
            - **apd_input** (str): Descriptor of the device and input port to use.
              Default is "Dev3/ai0". The Dev3 part is negotiated when you plug in
              your device. Please consult e.g. the NI software to find this value.
              the "ai0" selects the channel on your device which you will read from.
            - **sample_clk** (str): Selects the channel on the DAQ board used to
              time acquisition. e.g. (default) "PFI1". PFI stands for Programmable
              Function Interface.
            - **start_trig** (str): Selects the channel on the DAQ board to recieve
              a trigger to start the measurement process ('default "PFI0").
            - **max_samp_rate** (float): Sets the max sampling rate of the DAQ device.

          Note that these configuration attributes extend those from the
          :class:`Sensor` base class.
    """

    def __init__(self, configuration: dict[str, Any]) -> None:
        with install_hint("daq"):
            import nidaqmx
            import nidaqmx.constants

        self._nidaqmx = nidaqmx
        self.type: str = "DAQ"
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

    def __repr__(self) -> str:
        return f"DAQ(configuration: {self.initial_configuration_dict})"

    def __str__(self) -> str:
        return f"NI-DAQ sensor instance (nidaqmx): DAQ(configuration: {self.initial_configuration_dict})"

    def open(self) -> None:
        """
        Configures an analog input voltage channel,
        sample clock and start trigger.
        """
        try:
            self.NsampsPerDAQread: float = self.number_measurements
            self.daq_task = self._nidaqmx.Task()
            self._create_analog_input_channel()
            self._configure_sample_clock()
            self._configure_analog_input_trigger()
            logger.info("DAQ opened and created read task".ljust(65, "."))
        except Exception:
            self.close()
            logger.exception("An error occured while trying opening the DAQ")
            traceback.print_exc()
            raise Exception(
                "An error Occured while opening and configureing the DAQ.\
                        Please make sure the DAQ is connected and powered on"
            )

    def _create_analog_input_channel(self) -> None:
        # create analog channel to measure voltage
        _ = self.daq_task.ai_channels.add_ai_voltage_chan(
            self.daq_apd_input,
            "",
            self._nidaqmx.constants.TerminalConfiguration.RSE,
            self.min_voltage,
            self.max_voltage,
            self._nidaqmx.constants.VoltageUnits.VOLTS,
        )

    def _configure_analog_input_trigger(self) -> None:
        # Configure convert clock
        # Specifies the terminal of the signal to use
        # as the AI Convert Clock.
        self.daq_task.timing.ai_conv_src = self.daq_sample_clk
        self.daq_task.timing.ai_conv_active_edge = self._nidaqmx.constants.Edge.RISING
        read_start_trig = self.daq_task.triggers.start_trigger
        # Configures the task to start acquiring samples
        # on the active edge of a digital signal.
        read_start_trig.cfg_dig_edge_start_trig(
            self.daq_start_trig, self._nidaqmx.constants.Edge.RISING
        )

    def _configure_sample_clock(self) -> None:
        # Configure sample clock : Sets the clock source, the clock rate,
        # active clock edge, sample mode and the number of
        # samples to acquire.
        self.daq_task.timing.cfg_samp_clk_timing(
            self.daq_max_sampling_rate,
            self.daq_sample_clk,
            self._nidaqmx.constants.Edge.RISING,
            self._nidaqmx.constants.AcquisitionType.FINITE,
            self.NsampsPerDAQread,
        )

    def acquire_data(self, synchroniser: Synchroniser | None = None) -> np.ndarray:
        """
        Reads all samples at onec from the configure sensor.

        See :meth:`Sensor.acquire_data`.
        """
        if synchroniser is not None:
            self.daq_task.start()
            synchroniser.trigger()
        try:
            samples = self.daq_task.read(self.NsampsPerDAQread, self.daq_timeout)
            self.daq_task.stop()
        except Exception as excpt:
            print(
                """Error: could not read DAQ.
                Please check your DAQ's connections.
                Exception details:""",
                type(excpt).__name__,
                ".",
                excpt,
            )
            sys.exit()
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
        """
        See :meth:`Sensor.close`
        """
        if not hasattr(self, "daq_task"):
            # Setup can fail before DAQ.open() creates the task; close should not
            # hide the original setup error with a secondary AttributeError.
            return
        self.daq_task.close()
        logger.info("DAQ closed".ljust(65, "."))
