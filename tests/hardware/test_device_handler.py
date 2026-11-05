"""DeviceHandler (static devices) and DynamicDeviceHandler tests.

A recording test device replaces real hardware: it remembers how it was
created and every call the handlers make. The handlers are used like in
main.py / run_measurement.py: update_devices() per measurement, then
set_all_params() (static) or next_dynamic_step() per step (dynamic).
"""

from typing import Any

import numpy as np
import pytest

from qupyt.hardware.device import Device, signal_mock
from qupyt.hardware.device.signal_mock import MockSignalSource
from qupyt.hardware.device_handler import DeviceHandler, DynamicDeviceHandler
from qupyt.mixins import UnknownDeviceError


class RecordingDevice(Device):
    """Accepts any config key and records what the handler does with it."""

    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        super().__init__(configuration)
        self.address = address
        self.created_with = configuration
        self.applied: list[dict[str, Any]] = []  # one entry per set_values()
        self.closed = False

    def set_values(self) -> None:
        self.applied.append(dict(self.configuration or {}))

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def recording_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register RecordingDevice as 'Rec' in a private copy of the registry."""
    monkeypatch.setattr(
        Device, "_registry", {**Device._registry, "Rec": RecordingDevice}
    )
    monkeypatch.setattr(signal_mock, "sleep", lambda _: None)  # Mock setters


def entry(
    config: dict[str, Any], address: str = "addr-1", device_type: str = "Rec"
) -> dict[str, Any]:
    """One device entry as it appears in the measurement YAML."""
    return {"device_type": device_type, "address": address, "config": config}


# --- static devices: opening ---------------------------------------------------------


def test_update_devices_creates_requested_devices() -> None:
    handler = DeviceHandler({})
    handler.update_devices(
        {
            "mw": entry({"frequency": 1}, address="A"),
            "laser": entry({"power": 2}, address="B"),
        }
    )

    assert set(handler.devices) == {"mw", "laser"}
    mw = handler.devices["mw"]["device"]
    assert isinstance(mw, RecordingDevice)
    assert mw.address == "A"
    assert mw.created_with == {"frequency": 1}


def test_creating_does_not_apply_values() -> None:
    handler = DeviceHandler({})
    handler.update_devices({"mw": entry({"frequency": 1})})
    assert handler.devices["mw"]["device"].applied == []


def test_set_all_params_applies_every_config() -> None:
    handler = DeviceHandler({})
    handler.update_devices(
        {
            "mw": entry({"frequency": 1}, address="A"),
            "laser": entry({"power": 2}, address="B"),
        }
    )
    handler.set_all_params()

    assert handler.devices["mw"]["device"].applied == [{"frequency": 1}]
    assert handler.devices["laser"]["device"].applied == [{"power": 2}]


def test_works_with_the_real_mock_signal_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Integration check: a registered device goes through the whole handler path.
    handler = DeviceHandler({})
    handler.update_devices(
        {"mw": entry({"frequency": ["channel_1", 3.7e9]}, device_type="Mock")}
    )
    with caplog.at_level("INFO"):
        handler.set_all_params()
    assert isinstance(handler.devices["mw"]["device"], MockSignalSource)
    assert any("set frequency channel 1" in r.getMessage() for r in caplog.records)


def test_unknown_device_type_raises() -> None:
    with pytest.raises(UnknownDeviceError):
        DeviceHandler({}).update_devices({"mw": entry({}, device_type="NotADevice")})


def test_requested_devices_are_copied() -> None:
    requested = {"mw": entry({"frequency": 1})}
    handler = DeviceHandler({})
    handler.update_devices(requested)
    requested["mw"]["config"]["frequency"] = 99
    assert handler.requested_devices["mw"]["config"] == {"frequency": 1}


# --- static devices: next measurement --------------------------------------------------


def test_same_name_and_address_reuses_the_device() -> None:
    handler = DeviceHandler({})
    handler.update_devices({"mw": entry({"frequency": 1})})
    first = handler.devices["mw"]["device"]

    handler.update_devices({"mw": entry({"frequency": 2})})

    assert handler.devices["mw"]["device"] is first
    assert not first.closed


def test_reused_device_applies_the_new_config() -> None:
    # Regression test: the reused device used to keep the config of the
    # measurement that created it (only the handler's dict was updated).
    handler = DeviceHandler({})
    handler.update_devices({"mw": entry({"frequency": 1})})
    handler.update_devices({"mw": entry({"frequency": 2})})
    handler.set_all_params()

    assert handler.devices["mw"]["device"].applied == [{"frequency": 2}]


def test_changed_address_closes_old_and_opens_new() -> None:
    handler = DeviceHandler({})
    handler.update_devices({"mw": entry({}, address="A")})
    old = handler.devices["mw"]["device"]

    handler.update_devices({"mw": entry({}, address="B")})

    assert old.closed
    new = handler.devices["mw"]["device"]
    assert new is not old
    assert new.address == "B"


def test_device_no_longer_requested_is_closed_and_removed() -> None:
    handler = DeviceHandler({})
    handler.update_devices(
        {"mw": entry({}, address="A"), "laser": entry({}, address="B")}
    )
    laser = handler.devices["laser"]["device"]

    handler.update_devices({"mw": entry({}, address="A")})

    assert laser.closed
    assert set(handler.devices) == {"mw"}


@pytest.mark.parametrize("requested", [{}, None])
def test_empty_request_closes_everything(requested: Any) -> None:
    # main.py calls update_devices({}) after every measurement and on shutdown.
    handler = DeviceHandler({})
    handler.update_devices({"mw": entry({})})
    mw = handler.devices["mw"]["device"]

    handler.update_devices(requested)

    assert mw.closed
    assert handler.devices == {}


# --- dynamic devices -------------------------------------------------------------------


def dynamic_handler(config: dict[str, Any], steps: int = 3) -> DynamicDeviceHandler:
    handler = DynamicDeviceHandler({}, number_dynamic_steps=steps)
    handler.update_devices({"mw": entry(config)})
    return handler


def run_steps(handler: DynamicDeviceHandler) -> list[dict[str, Any]]:
    for _ in range(handler.number_dynamic_steps):
        handler.next_dynamic_step()
    applied: list[dict[str, Any]] = handler.devices["mw"]["device"].applied
    return applied


def test_dynamic_device_is_created_with_empty_config() -> None:
    handler = dynamic_handler({"frequency": [1.0, 3.0]})
    assert handler.devices["mw"]["device"].created_with == {}


def test_two_values_are_a_linear_range() -> None:
    applied = run_steps(dynamic_handler({"frequency": [2.0e9, 3.7e9]}))
    assert applied == [
        {"frequency": [("channel_1", 2.0e9)]},
        {"frequency": [("channel_1", 2.85e9)]},
        {"frequency": [("channel_1", 3.7e9)]},
    ]


def test_explicit_list_is_used_as_is() -> None:
    applied = run_steps(dynamic_handler({"frequency": [5, 1, 3]}))
    assert [a["frequency"] for a in applied] == [
        [("channel_1", 5)],
        [("channel_1", 1)],
        [("channel_1", 3)],
    ]


def test_explicit_list_must_match_number_of_steps() -> None:
    with pytest.raises(ValueError, match="number of dynamic_steps matches"):
        dynamic_handler({"frequency": [1, 2, 3, 4]}, steps=3)


@pytest.mark.parametrize(
    "config_value",
    [
        ["channel_2", [1.0, 3.0]],  # one channel, as written in YAML
        [["channel_2", [1.0, 3.0]]],  # list of channels
    ],
)
def test_channel_can_be_given(config_value: Any) -> None:
    applied = run_steps(dynamic_handler({"frequency": config_value}))
    assert [a["frequency"] for a in applied] == [
        [("channel_2", 1.0)],
        [("channel_2", 2.0)],
        [("channel_2", 3.0)],
    ]


def test_several_parameters_and_channels_per_step() -> None:
    # Same shape as the ODMR example: amplitude constant, frequency swept.
    applied = run_steps(
        dynamic_handler(
            {
                "amplitude": ["channel_1", [1, 1]],
                "frequency": [["channel_1", [1.0, 3.0]], ["channel_2", [10.0, 30.0]]],
            }
        )
    )
    assert applied[1] == {
        "amplitude": [("channel_1", 1.0)],
        "frequency": [("channel_1", 2.0), ("channel_2", 20.0)],
    }


def test_next_measurement_restarts_at_step_zero() -> None:
    handler = dynamic_handler({"frequency": [1.0, 3.0]})
    handler.next_dynamic_step()
    handler.next_dynamic_step()

    handler.update_devices({"mw": entry({"frequency": [10.0, 30.0]})})
    handler.next_dynamic_step()

    assert handler.devices["mw"]["device"].applied[-1] == {
        "frequency": [("channel_1", 10.0)]
    }


def test_sweep_values_are_numpy_linspace() -> None:
    handler = dynamic_handler({"frequency": [0.0, 1.0]}, steps=5)
    np.testing.assert_allclose(
        handler.devices["mw"]["sweep_lists"]["frequency"]["channel_1"],
        np.linspace(0.0, 1.0, 5),
    )
