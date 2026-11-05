"""
qupyt.mixins: ConfigurationMixin (applying a configuration dict through the
attribute_map) and the configuration error classes.
"""
from typing import Any

import pytest

from qupyt.mixins import (
    ConfigurationError,
    ConfigurationMixin,
    UnknownDeviceError,
    UnknownHardwareTypeError,
    UnknownSensorError,
    UnknownSynchroniserError,
)


class Configurable(ConfigurationMixin):
    """Minimal user of the mixin; records every setter call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.attribute_map = {
            "frequency": lambda v: self.calls.append(("frequency", v)),
            "amplitude": lambda v: self.calls.append(("amplitude", v)),
            "broken": self._broken_setter,
        }

    def _broken_setter(self, value: Any) -> None:
        # Like a VISA source looking up a command it does not have.
        raise KeyError("SetFreq1")


def test_setters_are_called_in_config_order_with_their_value():
    device = Configurable()
    device._update_from_configuration({"amplitude": -10, "frequency": 2.87e9})
    assert device.calls == [("amplitude", -10), ("frequency", 2.87e9)]


def test_empty_configuration_calls_nothing():
    device = Configurable()
    device._update_from_configuration({})
    assert device.calls == []


def test_unknown_key_raises_key_error():
    with pytest.raises(KeyError, match="Unknown attribute: frequncy"):
        Configurable()._update_from_configuration({"frequncy": 1})


def test_unknown_key_error_is_not_chained():
    # `raise ... from None`: the traceback shows one error, not the internal
    # dict lookup "during handling of the above exception".
    with pytest.raises(KeyError) as exc_info:
        Configurable()._update_from_configuration({"nope": 1})
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__


def test_keys_before_an_unknown_key_are_already_applied():
    device = Configurable()
    with pytest.raises(KeyError):
        device._update_from_configuration({"frequency": 1, "nope": 2, "amplitude": 3})
    assert device.calls == [("frequency", 1)]


def test_key_error_inside_a_setter_is_not_reported_as_unknown_attribute():
    # Regression: the try/except used to wrap the setter call as well, so this
    # surfaced as "Unknown attribute: broken" and hid the real cause.
    with pytest.raises(KeyError) as exc_info:
        Configurable()._update_from_configuration({"broken": 1})
    assert exc_info.value.args == ("SetFreq1",)
    assert "Unknown attribute" not in str(exc_info.value)


def test_other_errors_inside_a_setter_pass_through():
    device = Configurable()
    device.attribute_map["frequency"] = lambda v: int("not a number")
    with pytest.raises(ValueError, match="invalid literal"):
        device._update_from_configuration({"frequency": 1})


# --- error classes ------------------------------------------------------------------

def test_configuration_error_message_names_value_and_valid_options():
    error = ConfigurationError("Burst mode", "nonsense", ["off", "gate"])
    assert "nonsense" in str(error)
    assert "['off', 'gate']" in str(error)


@pytest.mark.parametrize("error_cls, kind", [
    (UnknownSensorError, "sensor"),
    (UnknownSynchroniserError, "synchroniser"),
    (UnknownDeviceError, "device"),
])
def test_unknown_hardware_error_message(error_cls, kind):
    error = error_cls("Foo", ["A", "B"])
    assert str(error) == f"Unknown {kind} type 'Foo'. Available: ['A', 'B']"
    assert error.config_param == f"the {kind} type"


@pytest.mark.parametrize("error_cls", [UnknownSensorError, UnknownSynchroniserError, UnknownDeviceError])
def test_unknown_hardware_errors_are_configuration_and_value_errors(error_cls):
    # Existing `except ConfigurationError` / `except ValueError` blocks keep catching them.
    assert issubclass(error_cls, UnknownHardwareTypeError)
    assert issubclass(error_cls, ConfigurationError)
    assert issubclass(error_cls, ValueError)
