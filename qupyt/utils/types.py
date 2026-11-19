from typing import TypeAlias

Scalar: TypeAlias = float | int | str
ChannelValue: TypeAlias = tuple[str, Scalar]
ParameterInput: TypeAlias = Scalar | ChannelValue | list[ChannelValue]

DynamicParameterInput = [
    list[float | int],
    tuple[str, list[float | int]],
    list[tuple[str, list[float | int]]],
]
