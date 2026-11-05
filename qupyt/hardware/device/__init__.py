"""Device classes.

Every module in this package is imported here, so each subclass registers
itself (``device_types=...``) and can be created with :meth:`Device.create`.
"""

import importlib
import pkgutil

from .base import Device

for _mod in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_mod.name}")

__all__ = ["Device"]
