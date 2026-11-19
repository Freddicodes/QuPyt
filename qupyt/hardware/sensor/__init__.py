"""Sensor classes.

Every module in this package is imported here, so each subclass registers
itself (``sensor_types=...``) and can be created with :meth:`Sensor.create`.
"""

import importlib
import logging
import pkgutil

from .base import Sensor

for _mod in pkgutil.iter_modules(__path__):
    try:
        importlib.import_module(f"{__name__}.{_mod.name}")
    except ImportError as exc:
        logging.getLogger(__name__).warning("Skipping %s: %s", _mod.name, exc)

__all__ = ["Sensor"]
