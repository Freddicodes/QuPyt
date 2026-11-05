"""Synchroniser classes.

Every module in this package is imported here, so each subclass registers
itself (``synchroniser_types=...``) and can be created with
:meth:`Synchroniser.create`.
"""

import importlib
import pkgutil

from .base import Synchroniser

for _mod in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_mod.name}")

__all__ = ["Synchroniser"]
