from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.pulse_sequences.pulse_sequence_handler import update_params_dict

DEVICE = {"mw": {"device_type": "Mock", "address": "a", "config": {"frequency": 1}}}


def test_device_handlers_create_devices():
    static = DeviceHandler({})
    static.update_devices(DEVICE)
    assert static.devices["mw"]["device"] is not None

    dynamic = DynamicDeviceHandler({}, number_dynamic_steps=3)
    dynamic.update_devices(
        {"mw": {"device_type": "Mock", "address": "a", "config": {"frequency": [1, 3]}}}
    )
    dynamic.next_dynamic_step()
    assert dynamic.current_dynamic_step == 1


def test_update_params_dict_replaces_none_and_scalars():
    update = {"dynamic_devices": {"mw": {"freq": 1}}}
    assert update_params_dict({"dynamic_devices": None}, update) == update
    assert update_params_dict({"dynamic_devices": 5}, update) == update
    assert update_params_dict({"a": {"b": 1}}, {"a": {"c": 2}}) == {"a": {"b": 1, "c": 2}}
