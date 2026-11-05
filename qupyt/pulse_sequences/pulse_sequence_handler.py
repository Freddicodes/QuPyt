"""
Run the user's pulse sequence script and merge its results into the
measurement parameters.

The measurement YAML names a Python file (``ps_path``) that defines
``generate_sequence(params)``. :func:`write_user_ps` imports and calls it
(it writes the ``sequence_<n>.yaml`` files); the dict it may return is
merged into the measurement parameters with :func:`update_params_dict`.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast


# pylint: disable=too-few-public-methods
class UserPulseSeqProtocol(Protocol):
    """
    Protocol to define that every generate_sequence
    function takes a dict as input and returns either
    None or another dict.
    """

    def generate_sequence(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """
        :param params: Input to pass the parameters needed
         to constuct the pulse sequence.
        :type params: dict[str, any]
        :return: Either returns a dictionary with updated, or dependent
         parameters, or None.
        :rtype: "dict[str, any] | None"
        """


def _load_module_from_path(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("user_pulse_seq", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Module user_pulse_seq cannot be loaded from path {path}")
    module = importlib.util.module_from_spec(spec)
    # Ensure the loader can execute the module
    if hasattr(spec.loader, "exec_module"):
        spec.loader.exec_module(module)
    else:
        raise ImportError("The loader for user_pulse_seq doesn't support execution")
    return module


def write_user_ps(path: Path, params: dict[str, Any]) -> dict[str, Any] | None:
    """
    Load user specified pulse sequence definition and
    execute it to generate the pulse sequence.

    :param path: Python file defining ``generate_sequence(params)``
        (``ps_path`` in the measurement YAML).
    :type path: Path
    :param params: The ``pulse_sequence`` section of the measurement YAML.
    :type params: dict[str, Any]
    :return: What ``generate_sequence`` returns: parameters to merge into
        the measurement parameters, or ``None``.
    :rtype: dict[str, Any] | None
    :raises FileNotFoundError: if the file does not exist.
    :raises ImportError: if the file cannot be loaded as a module.
    """
    user_ps = cast(UserPulseSeqProtocol, _load_module_from_path(path))
    dependent_parameters = user_ps.generate_sequence(params)
    return dependent_parameters


def update_params_dict(
    params: dict[str, Any], update_params: dict[str, Any] | None
) -> dict[str, Any]:
    """
    Update the parameters dictionary. This is done via recursion to reach
    nested parameter dicts without overwriting the full config.

    :param params: Full configuration dictionary to be updated (in place).
    :type params: dict[str, Any]
    :param update_params: Dictionary with the values to be updated, or ``None``.
    :type update_params: dict[str, Any] | None
    :return: ``params`` (the same object), updated.
    :rtype: dict[str, Any]
    """
    if update_params is None:
        return params
    for key, value in update_params.items():
        if isinstance(value, dict):
            params[key] = update_params_dict(params.get(key, {}), value)
        else:
            params[key] = value
    return params
