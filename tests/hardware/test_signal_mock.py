"""
MockSignalSource: the configuration is stored on creation and applied by
set_values(); every setting is logged as a 'MOCKING! -> set …' line.
"""
import logging

import pytest

from qupyt.hardware.device import Device, signal_mock

ODMR_CONFIG = {
    "amplitude": [["channel_2", 1], ["channel_3", 3]],
    "frequency": ["channel_1", "3.7e9"],
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The Mock setters sleep 0.1 s per value."""
    monkeypatch.setattr(signal_mock, "sleep", lambda _: None)


def create(config: dict) -> Device:
    return Device.create("Mock", "mock-address", config)


def mock_settings(caplog) -> list[str]:
    """The 'MOCKING! -> set …' log lines, without the dot padding."""
    return [
        " ".join(r.getMessage().replace(".", " ").split())
        for r in caplog.records
        if r.getMessage().startswith("MOCKING!")
    ]


def test_creating_does_not_apply_the_config(caplog):
    with caplog.at_level(logging.INFO):
        create(ODMR_CONFIG)
    assert mock_settings(caplog) == []


def test_set_values_applies_the_config(caplog):
    device = create(ODMR_CONFIG)
    with caplog.at_level(logging.INFO):
        device.set_values()
    assert mock_settings(caplog) == [
        "MOCKING! -> set amplitude channel 2 to 1",
        "MOCKING! -> set amplitude channel 3 to 3",
        "MOCKING! -> set frequency channel 1 to 3700000000 0",  # "3.7e9" str -> float
    ]


def test_update_configuration_replaces_the_config(caplog):
    device = create(ODMR_CONFIG)
    device.update_configuration({"frequency": ["channel_1", "2.0e9"], "phase": 90})
    with caplog.at_level(logging.INFO):
        device.set_values()
    assert mock_settings(caplog) == [
        "MOCKING! -> set frequency channel 1 to 2000000000 0",  # "2.0e9" str -> float
        "MOCKING! -> set phase channel 1 to 90",
    ]


def test_update_configuration_alone_sets_nothing(caplog):
    device = create({})
    with caplog.at_level(logging.INFO):
        device.update_configuration({"frequency": 1})
    assert mock_settings(caplog) == []


def test_unknown_config_key_raises_on_set_values():
    device = create({"not_a_setting": 1})
    with pytest.raises(KeyError, match="Unknown attribute: not_a_setting"):
        device.set_values()
