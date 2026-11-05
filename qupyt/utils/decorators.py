"""Contains all decorators used across QuPyt.

Device setters are stacked like this::

    @validate_call
    @coerce_device_config_shape
    @loop_inputs
    def set_frequency(self, freq: ChannelValue) -> None:
        channel, value = freq
        ...

The setter body receives one ``(channel, value)`` pair (:data:`ChannelValue`).
Callers (and pydantic's ``validate_call``) see the user-facing
:data:`ParameterInput` instead: a scalar, one pair or a list of pairs.
"""

import inspect
import logging
from collections.abc import Callable
from functools import wraps
from time import perf_counter
from typing import Any, Union

from numpy.typing import NDArray

logger = logging.getLogger(__name__)

Scalar = float | int | str
ChannelValue = tuple[str, Scalar]
ParameterInput = Union[Scalar, ChannelValue, list[ChannelValue]]  # noqa: UP007


def _expose_parameter_input(
    wrapper: Callable[..., None], func: Callable[..., None]
) -> None:
    """Make ``wrapper`` look like ``(self, <name>: ParameterInput) -> None``.

    ``functools.wraps`` copies the setter's ``__annotations__`` and sets
    ``__wrapped__``, which ``inspect.signature`` follows. ``validate_call``
    would then validate user input against the setter's ``ChannelValue``
    annotation and reject scalars. ``inspect.signature`` stops at an
    explicit ``__signature__``, so set that (and ``__annotations__``) here.
    """
    signature = inspect.signature(func)
    self_param, value_param, *rest = signature.parameters.values()
    value_param = value_param.replace(annotation=ParameterInput)
    # setattr: mypy does not know __signature__ on a Callable.
    setattr(  # noqa: B010
        wrapper,
        "__signature__",
        signature.replace(
            parameters=[self_param, value_param, *rest], return_annotation=None
        ),
    )
    wrapper.__annotations__ = {value_param.name: ParameterInput, "return": None}


def coerce_device_config_shape[S](
    func: Callable[[S, list[ChannelValue]], None],
) -> Callable[[S, ParameterInput], None]:
    """Coerces specific configuration input to QuPyt devices to adhere to a unified shape.

    User configuration can thus be kept simple. Their shape is adapted in this
    decorator:

    - ``5`` → ``[("channel_1", 5)]``
    - ``("channel_2", 5)`` → ``[("channel_2", 5)]``
    - a list of pairs is passed on unchanged.

    Raises
    ------
        ValueError: for any other input shape.
    """

    @wraps(func)
    def wrapper(self: S, arg: ParameterInput) -> None:
        if isinstance(arg, list):
            func(self, arg)
        elif isinstance(arg, tuple):
            func(self, [arg])
        elif isinstance(arg, (float, int, str)):
            func(self, [("channel_1", arg)])
        else:
            # ValueError (not TypeError) kept for existing callers.
            raise ValueError(
                f"Unsupported device parameter input: {arg!r}"
            )  # noqa: TRY004

    _expose_parameter_input(wrapper, func)
    return wrapper


def _parse_number(value: str) -> int | float | str:
    # int first, so "1" stays "1" in commands like f"o{value}" (not "1.0").
    for convert in (int, float):
        try:
            return convert(value)
        except ValueError:
            pass
    return value


def loop_inputs[S](
    func: Callable[[S, ChannelValue], None],
) -> Callable[[S, list[ChannelValue]], None]:
    """Call ``func`` once per ``(channel, value)`` pair.

    ``"channel_1"`` is reduced to ``"1"``. String values that look like
    numbers (e.g. quoted YAML values) are converted: ``"1"`` to ``int``,
    ``"3.7e9"`` to ``float``. Other strings (``"off"``) are passed on
    unchanged.
    """

    @wraps(func)
    def wrapper(self: S, arg: list[ChannelValue]) -> None:
        for inp in arg:
            if (
                not isinstance(inp, (tuple, list))
                or len(inp) != 2
                or not isinstance(inp[0], str)
            ):
                raise ValueError(f"Expected a (channel, value) pair; got {inp!r}")
            channel = inp[0].split("_")[-1]
            value = inp[1]
            if isinstance(value, str):
                value = _parse_number(value)
            func(self, (channel, value))

    return wrapper


def log_sensor_acquisition[**P, A: NDArray[Any]](
    func: Callable[P, A],
) -> Callable[P, A]:
    """Log acquisition details at the sensor boundary, including direct calls.

    At DEBUG level the start, the resulting shape and dtype and the elapsed
    time are logged; a failing acquisition is logged with its traceback and
    re-raised.

    Parameters
    ----------
    func : Callable
        A sensor's ``acquire_data`` method.

    Returns
    -------
    Callable
        The wrapped method.
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> A:
        sensor_name = type(args[0]).__name__ if args else "sensor"
        logger.debug(
            "Starting %s acquisition (measurements=%s)",
            sensor_name,
            getattr(args[0], "number_measurements", "?") if args else "?",
        )
        started = perf_counter()
        try:
            data = func(*args, **kwargs)
        except Exception:
            logger.exception(
                "%s acquisition failed after %.3fs",
                sensor_name,
                perf_counter() - started,
            )
            raise
        logger.debug(
            "Acquired data from %s: shape=%s dtype=%s elapsed=%.3fs",
            sensor_name,
            data.shape,
            data.dtype,
            perf_counter() - started,
        )
        return data

    return wrapper
