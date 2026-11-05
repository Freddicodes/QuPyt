"""
Connection handling for all devices that communicate via the VISA library.

:class:`VisaObject` opens and closes the VISA resource and waits for
operation complete (OPC). It does not know any device types: each device
class declares its own command set in the ``COMMANDS`` class attribute
(e.g. ``"SetFreq1": "FREQ "``). The default command set only contains
``"OPC"``.
"""

import logging
from time import sleep
from typing import ClassVar

import pyvisa
from pyvisa.resources import MessageBasedResource

logger = logging.getLogger(__name__)

class VisaObject:
    """
    Parent class for all devices that connect via the VISA protocol.

    Subclasses override ``COMMANDS`` with their device-specific command set;
    it is available on the instance as ``self.command``.
    """

    COMMANDS: ClassVar[dict[str, str]] = {"OPC": "*OPC?"}

    def __init__(self, handle: str, s_type: str) -> None:
        """
        handle: VISA address of the device, e.g. "TCPIP::1.2.3.4::INSTR"
        s_type: registered device type (e.g. "SRS", "SMB"), used in log messages
        """
        self.handle = handle
        self.s_type = s_type
        self.command = self.COMMANDS
        resource_manager = pyvisa.ResourceManager()

        try:
            resource = resource_manager.open_resource(handle)
        except Exception as e:
            resource_manager.close()
            resource_manager.visalib._registry.clear()
            raise ConnectionError(
                f"Could not open {s_type} at VISA address {handle}"
            ) from e
        # open_resource() is typed as returning Resource; QuPyt needs
        # write/query, which only message-based resources (INSTR, SOCKET, ...)
        # have. pyvisa still picks the concrete class (e.g. TCPIPInstrument).
        if not isinstance(resource, MessageBasedResource):
            resource.close()
            resource_manager.close()
            raise TypeError(
                f"{s_type} at VISA address {handle} is not a message-based "
                f"resource (got {type(resource).__name__})"
            )
        self.instance: MessageBasedResource = resource
        self.instance.timeout = 60000

        logger.info(f"Opening {s_type} at address {handle}".ljust(65, ".") + "[done]")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(handle: {self.handle}, s_type: {self.s_type})"

    def __str__(self) -> str:
        return f"{type(self).__name__}(handle: {self.handle}, s_type: {self.s_type})"

    def opc_wait(self) -> None:
        """
        Wait until the device reports operation complete: query the
        ``"OPC"`` command up to 3 times, 0.1 s apart, until it returns a
        non-zero number.

        :raises TimeoutError: if no valid non-zero response arrives.
        """
        last_response = ""
        for attempt in range(3):
            opc = self.instance.query(self.command["OPC"])
            last_response = opc
            try:
                if float(opc.strip()) != 0:
                    return
            except ValueError:
                logger.warning(
                    f"{self.s_type} returned invalid OPC response {opc!r}; "
                    f"retrying ({attempt + 1}/3)"
                )
            sleep(0.1)
        raise TimeoutError(
            f"{self.s_type} did not return a valid OPC response. "
            f"Last response was {last_response!r}."
        )

    def close(self) -> None:
        """
        Close the VISA resource and clear pyvisa's resource registry.
        Errors are logged, not raised, so closing never fails a measurement.
        """
        try:
            self.instance.close()
            self.instance.visalib._registry.clear()
            logger.info(f"Closing {self.s_type} at address {self.handle}".ljust(65, ".") + "[done]")
        except Exception:
            logger.exception(
                f"Closing {self.s_type} at address {self.handle} failed".ljust(65, ".") + "[failed]"
            )
