type Scalar = float | int | str
type ChannelValue = tuple[str, Scalar]
type ParameterInput = Scalar | ChannelValue | list[ChannelValue]

type DynamicParameterInput = (
    list[float | int]
    | tuple[str, list[float | int]]
    | list[tuple[str, list[float | int]]]
)
