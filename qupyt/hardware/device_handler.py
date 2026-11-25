"""
This file handles all aspect of the various signal sources in
active use. Newly requested devices are opened and added
to a dictionary of used devices, while no longer needed ones are close
and deleted.
Edge cases like no requested devices are handeled.

Device configuration and various output values to be set
or sweeped are handled here as well.
"""

import copy
import logging
from typing import Any

import numpy as np
from pydantic import validate_call

from qupyt.hardware.device.base import Device
from qupyt.utils.logging import DONE
from qupyt.utils.types import DynamicParameterInput

logger = logging.getLogger(__name__)


class DeviceHandler:
    """
    A class to handle the management of devices for measurements.

    This class provides methods to update the list of requested devices,
    close superfluous devices, open new requested devices, and set parameters
    for all active devices.

    From the point of view of this class, all devices are static for the
    duration of one measurement.
    """

    def __init__(
        self,
        requested_devices: dict[str, Any],
    ) -> None:
        """
        Initialize the DeviceHandler with a dictionary of requested devices.

        :param requested_devices: A dictionary containing the initial requested
                                  devices configuration.
        :type requested_devices: dict[str, Any]
        """
        self.requested_devices: dict[str, Any] = {}
        self.devices: dict[str, Any] = {}
        self.update_requested_device_dict(requested_devices)

    def update_devices(self, requested_devices: dict[str, Any]) -> None:
        """
        Update the device lists by closing all superfluous devices and opening
        newly requested devices.

        :param requested_devices: A dictionary containing the updated requested
                                  devices configuration.
        :type requested_devices: dict[str, Any]
        """
        if requested_devices is None:
            self.requested_devices = {}
        else:
            self.requested_devices = copy.deepcopy(requested_devices)
        self.close_superfluous_devices()
        self.open_new_requested_devices()

    def close_superfluous_devices(self) -> None:
        """
        Close all devices not requested for the next measurement.

        This method compares the dictionaries of requested and existing devices,
        and closes and removes devices that are not requested.
        """
        requested_name_address_tuples = [
            (key, val["address"]) for key, val in self.requested_devices.items()
        ]
        for key, value in list(self.devices.items()):
            if (key, value["address"]) not in requested_name_address_tuples:
                self.devices[key]["device"].close()
                rem = self.devices.pop(key)
                logger.info(f"Removed {rem} from active devices dict", extra=DONE)

    def open_new_requested_devices(self) -> None:
        """
        Open all devices requested for the next measurement that are not in the
        current active dictionary.

        This method compares the dictionaries of requested and existing devices,
        and opens and adds devices that are newly requested.
        """
        current_name_address_tuples = [
            (key, val["address"]) for key, val in self.devices.items()
        ]
        for key, value in list(self.requested_devices.items()):
            if (key, value["address"]) not in current_name_address_tuples:
                device = Device.create(value)
                self.devices[key] = value
                self.devices[key]["device"] = device
                logger.info(
                    f"Added {self.devices[key]['device']!r} to active devices dict",
                    extra=DONE,
                )
            else:
                self.devices[key]["config"] = value["config"]
                logger.info(
                    f"Updated {self.devices[key]['device']!r} in active devices dict",
                    extra=DONE,
                )

    def set_all_params(self) -> None:
        """
        Set all values requested for static devices.

        This method sets the parameters for all active devices as per the
        requested configuration.
        """
        for value in self.devices.values():
            value["device"].set_values()

    def update_requested_device_dict(
        self,
        requested_devices: dict[str, Any],
    ) -> None:
        """
        Create a deep copy of the requested devices dictionary to avoid altering
        the original configuration.

        :param requested_devices: Full configuration dictionary as loaded from
                                  config YAML.
        :type requested_devices: dict[str, Any]
        """
        self.requested_devices = copy.deepcopy(requested_devices)


class DynamicDeviceHandler(DeviceHandler):
    """
    A class to handle the management of dynamic devices for measurements,
    inheriting from DeviceHandler.

    This class extends the DeviceHandler to support dynamic device configurations
    that change over a series of steps.

    The devices are still treated as static. This class updates their
    configuration for every dynamic step.
    """

    def __init__(
        self, requested_devices: dict[str, Any], number_dynamic_steps: int = 1
    ) -> None:
        """
        Initialize the DynamicDeviceHandler with a dictionary of requested devices
        and the number of dynamic steps.

        :param requested_devices: A dictionary containing the initial requested
                                  devices configuration.
        :type requested_devices: dict[str, Any]
        :param number_dynamic_steps: The number of dynamic steps for device
                                     configuration changes, defaults to 1.
        :type number_dynamic_steps: int
        """
        self.number_dynamic_steps = number_dynamic_steps
        self.current_dynamic_step = 0
        super().__init__(requested_devices)

    def open_new_requested_devices(self) -> None:
        """
        Open all devices requested for the next measurement that are not in the
        current active dictionary.

        This method compares the dictionaries of requested and existing devices,
        and opens and adds devices that are newly requested.

        Furhter, it prepares the
        devices for dynamic configuration changes.
        """
        current_name_address_tuples = [
            (key, val["address"]) for key, val in self.devices.items()
        ]
        for key, value in list(self.requested_devices.items()):
            if (key, value["address"]) not in current_name_address_tuples:
                # Remove config dicts.
                # These will be updated for every new value.
                creation_dict = copy.deepcopy(value)
                creation_dict["config"] = {}
                device = Device.create(creation_dict)
                self.devices[key] = value
                self.devices[key]["device"] = device
                self.devices[key]["sweep_config"] = copy.deepcopy(value["config"])
                logger.info(
                    f"Added {self.devices[key]['device']!r} to active devices dict",
                    extra=DONE,
                )
            else:
                self.devices[key]["sweep_config"] = copy.deepcopy(value["config"])
                logger.info(
                    f"Updated {self.devices[key]['device']!r} in active devices dict",
                    extra=DONE,
                )
        self._reset_step_counter()
        self._make_sweep_lists()

    def next_dynamic_step(self) -> None:
        """
        Apply next dynamic values for dynamic devices based on the current
        dynamic step and increment the step counter.

        This method updates the configuration of each device to the values
        corresponding to the current dynamic step and applies these values.
        """
        for device in self.devices.values():
            current_config = {}
            for parameter, channel_config in device["sweep_lists"].items():
                current_config[parameter] = []
                for channel, sweep_values in channel_config.items():
                    current_config[parameter].append(
                        (channel, sweep_values[self.current_dynamic_step])
                    )
            device["device"].update_configuration(current_config)
            device["device"].set_values()
        self.current_dynamic_step += 1

    def _reset_step_counter(self) -> None:
        """
        Reset the dynamic step counter to zero.
        """
        self.current_dynamic_step = 0

    def _make_sweep_lists(self) -> None:
        """
        Construct arrays or lists of values to be swept for each dynamic device.

        This method prepares the sweep values for each device parameter based on
        the specified configuration and number of dynamic steps.
        """
        for device in self.devices.values():
            for parameter, value_list in device["sweep_config"].items():
                value_list = self._coerce_input_shape_dynamic(value_list)
                device.setdefault("sweep_lists", {}).setdefault(parameter, {})
                for channel, value_range in value_list:
                    if len(value_range) == 2:
                        device["sweep_lists"][parameter][channel] = np.linspace(
                            value_range[0], value_range[1], self.number_dynamic_steps
                        )
                    else:
                        if len(value_range) != self.number_dynamic_steps:
                            raise ValueError(
                                "Trying to set manual sweep value list. Please make sure the number of dynamic_steps matches the length of the provided list"
                            )
                        device["sweep_lists"][parameter][channel] = value_range

    @validate_call
    def _coerce_input_shape_dynamic(self, arg: DynamicParameterInput):
        """
        Use pydantic to enforce correct type.
        Based on this parse the input into a defined type of:

        list[tuple[str, list[Union[float, int]]]]

        This makes further handling very easy.
        """
        if isinstance(arg, list) and all(isinstance(item, tuple) for item in arg):
            return arg
        if isinstance(arg, tuple):
            return [arg]
        if isinstance(arg, list):
            return [("channel_1", arg)]
        raise ValueError("Dynamic parameter coercion failed")
