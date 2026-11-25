"""
A shared mixin class to unify
how parameters are updated or set from
a configuration dictionary.
"""

import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class UpdateConfigurationProtocol(Protocol):
    """
    Protocoll defining structure of the attribute_map
    for the mixin class.
    """

    attribute_map: dict[str, Callable[[Any], None]]


UpdateConfigurationType = dict[str, Callable[[Any], None]]


class ConfigurationMixin:
    """
    Mixin class to create a uniform update method
    for attributes an hardware values from a configuration
    dictionary.
    """

    def _update_from_configuration(
        self: UpdateConfigurationProtocol, configuration: dict[str, Any]
    ) -> None:
        for attr, value in configuration.items():
            try:
                setter = self.attribute_map[attr]
            except KeyError:
                logger.error(f"Unknown attribute: {attr}")
                raise KeyError(f"Unknown attribute: {attr}") from None
            setter(value)
