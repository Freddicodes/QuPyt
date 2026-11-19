"""
Helpers for importing optional hardware drivers.
"""

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def install_hint(extra: str | None = None, vendor: str | None = None) -> Iterator[None]:
    """Re-raise an ImportError inside the block with install instructions.

    Parameters
    ----------
    extra : str | None
        Name of the qupyt extra that installs the driver, e.g. ``'basler'``.
    vendor : str | None
        Instructions for drivers that are not on PyPI.

    Examples
    --------
    >>> with install_hint("basler"):
    ...     from pypylon import pylon
    """
    try:
        yield
    except ImportError as exc:
        missing = exc.name or "A required driver library"
        if extra is not None:
            how = f"Install it with: pip install qupyt[{extra}]"
        else:
            how = vendor or ""
        raise ImportError(f"{missing} is missing. {how}".strip(), name=exc.name) from exc
