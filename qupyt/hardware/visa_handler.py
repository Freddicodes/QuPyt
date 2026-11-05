"""Connection handling for all devices that communicate via the VISA library.

:class:`VisaObject` opens and closes the VISA resource and waits for operation
complete (OPC). It does not know any device types: each device class declares
its own command set in the ``COMMANDS`` class attribute (e.g.
``"SetFreq1": "FREQ "``). The default command set only contains ``"OPC"``.
"""

from __future__ import annotations

import logging
from time import sleep
from typing import ClassVar

import pyvisa
from pyvisa.resources import MessageBasedResource

from qupyt.utils.errors import cleanup_after_error

logger = logging.getLogger(__name__)


class VisaObject:
    """Base connection for devices controlled through the VISA protocol.

    Subclasses override ``COMMANDS`` with their device-specific command set; it
    is available on the instance as ``self.command``.
    """

    COMMANDS: ClassVar[dict[str, str]] = {"OPC": "*OPC?"}

    def __init__(self, handle: str, s_type: str) -> None:
        """Open the VISA resource.

        Parameters
        ----------
        handle : str
            VISA address of the device, e.g. ``"TCPIP::1.2.3.4::INSTR"``.
        s_type : str
            Registered device type (e.g. ``"SRS"``, ``"SMB"``), used in log
            messages.

        Raises
        ------
        ConnectionError
            If the resource cannot be opened.
        TypeError
            If the resource is not message-based (no write/query).
        """
        self.handle = handle
        self.s_type = s_type
        self.command = self.COMMANDS
        self._is_open = False
        # pyvisa shares one ResourceManager per backend: closing it here would
        # also close every other open VISA device, so only the resource is closed.
        resource_manager = pyvisa.ResourceManager()
        try:
            resource = resource_manager.open_resource(handle)
        except Exception as e:
            logger.exception("Failed to open %s at address %s", s_type, handle)
            raise ConnectionError(
                f"Could not open {s_type} at VISA address {handle}"
            ) from e
        # open_resource() is typed as returning Resource; QuPyt needs
        # write/query, which only message-based resources (INSTR, SOCKET, ...)
        # have. pyvisa still picks the concrete class (e.g. TCPIPInstrument).
        if not isinstance(resource, MessageBasedResource):
            cleanup_after_error(resource.close, f"VISA setup cleanup at {handle}")
            raise TypeError(
                f"{s_type} at VISA address {handle} is not a message-based "
                f"resource (got {type(resource).__name__})"
            )
        self.instance: MessageBasedResource = resource
        self._is_open = True
        try:
            self.instance.timeout = 60000
        except BaseException:
            logger.exception("Failed to configure %s at address %s", s_type, handle)
            cleanup_after_error(
                lambda: VisaObject.close(self), f"VISA setup cleanup at {handle}"
            )
            raise
        logger.info(f"Opening {s_type} at address {handle}".ljust(65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(handle: {self.handle}, s_type: {self.s_type})"

    def __str__(self) -> str:
        return f"{type(self).__name__}(handle: {self.handle}, s_type: {self.s_type})"

    def opc_wait(self) -> None:
        """Wait until the device reports operation complete.

        Query the ``"OPC"`` command up to 3 times, 0.1 s apart, until it
        returns a non-zero number.

        Raises
        ------
        TimeoutError
            If no valid non-zero response arrives.
        """
        last_response = ""
        logger.debug(
            "Waiting for %s at %s to complete pending operations",
            self.s_type,
            self.handle,
        )
        for attempt in range(3):
            try:
                opc = self.instance.query(self.command["OPC"])
            except Exception:
                logger.exception(
                    "Failed to query OPC for %s at %s", self.s_type, self.handle
                )
                raise
            last_response = opc
            try:
                if float(opc.strip()) != 0:
                    logger.debug(
                        "%s at %s completed pending operations",
                        self.s_type,
                        self.handle,
                    )
                    return
            except ValueError:
                logger.warning(
                    "%s returned invalid OPC response %r; retrying (%s/3)",
                    self.s_type,
                    opc,
                    attempt + 1,
                )
            sleep(0.1)
        logger.error(
            "%s at %s timed out waiting for OPC; last_response=%r",
            self.s_type,
            self.handle,
            last_response,
        )
        raise TimeoutError(
            f"{self.s_type} did not return a valid OPC response. "
            f"Last response was {last_response!r}."
        )

    def close(self) -> None:
        """Close the VISA resource.

        Safe to call more than once (and after a failed open).

        Raises
        ------
        Exception
            Whatever the resource raises while closing; it is logged first.
        """
        if not getattr(self, "_is_open", False):
            return
        try:
            self.instance.close()
            self._is_open = False
            logger.info(
                f"Closing {self.s_type} at address {self.handle}".ljust(65, ".")
                + "[done]"
            )
        except Exception:
            logger.exception(
                f"Closing {self.s_type} at address {self.handle} failed".ljust(65, ".")
                + "[failed]"
            )
            raise
