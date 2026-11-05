import pytest

from qupyt.utils.decorators import loop_inputs


class _Recorder:
    def __init__(self):
        self.calls = []

    @loop_inputs
    def set_value(self, inp):
        self.calls.append(inp)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3.7e9", 3.7e9),
        ("1", 1),
        (" 2 ", 2),
        ("off", "off"),
        (5, 5),
        (2.5, 2.5),
    ],
)
def test_loop_inputs_converts_numeric_strings(value, expected):
    recorder = _Recorder()
    recorder.set_value([("channel_1", value)])
    assert recorder.calls == [("1", expected)]
    assert type(recorder.calls[0][1]) is type(expected)


def test_loop_inputs_calls_once_per_channel():
    recorder = _Recorder()
    recorder.set_value([("channel_1", "1e6"), ("channel_2", 3)])
    assert recorder.calls == [("1", 1e6), ("2", 3)]


# --- full stack as used by the device setters --------------------------------

import inspect

from pydantic import ValidationError, validate_call

from qupyt.utils.decorators import (
    ChannelValue,
    ParameterInput,
    coerce_device_config_shape,
)


class _Device:
    def __init__(self):
        self.calls = []

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        self.calls.append(freq)


@pytest.mark.parametrize(
    ("user_input", "expected"),
    [
        (5, [("1", 5)]),
        ("3.7e9", [("1", 3.7e9)]),
        (("channel_2", 7), [("2", 7)]),
        (["channel_2", 7], [("2", 7)]),  # YAML list for one pair
        ([("channel_1", 1), ("channel_2", "2")], [("1", 1), ("2", 2)]),
    ],
)
def test_stack_accepts_every_user_shape(user_input, expected):
    device = _Device()
    device.set_frequency(user_input)
    assert device.calls == expected


def test_stack_rejects_other_shapes():
    with pytest.raises(ValidationError):
        _Device().set_frequency({"channel_1": 5})


def test_outer_signature_is_the_user_facing_one():
    # validate_call validates against this; it must not be the setter's
    # ChannelValue annotation (would reject scalars).
    def set_frequency(self, freq: ChannelValue) -> None:
        pass

    wrapper = coerce_device_config_shape(loop_inputs(set_frequency))
    parameter = inspect.signature(wrapper).parameters["freq"]
    assert parameter.annotation is ParameterInput
