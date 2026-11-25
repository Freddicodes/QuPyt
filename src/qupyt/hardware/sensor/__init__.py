"""Sensor classes.

Every module in this package is imported here, so each subclass registers
itself (``sensor_types=...``) and can be created with :meth:`Sensor.create`.
"""

import importlib
import pkgutil

from .base import Sensor

for _mod in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_mod.name}")

__all__ = ["Sensor"]
