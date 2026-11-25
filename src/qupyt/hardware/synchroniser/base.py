from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from qupyt.exceptions import UnknownSynchroniserError
from qupyt.mixins import (
    ConfigurationMixin,
    UpdateConfigurationType,
)

logger = logging.getLogger(__name__)


def _validate_synchroniser_sequence_mode(
    sequence_instructions: dict[str, Any],
    device_type: str,
    supported_modes: tuple[str, ...],
) -> str:
    """Reject sequence upload modes unsupported by the selected synchroniser.

    awg_sequence_mode is not part of the logical pulse program. It only
    describes how TekAWG should upload that pulse program. Non-AWG devices
    reject unsupported modes explicitly so a Tek-only YAML option is not
    silently ignored.
    """

    mode = str(sequence_instructions.get("awg_sequence_mode", "flat"))
    if mode not in supported_modes:
        message = (
            f"awg_sequence_mode: {mode} is not supported by {device_type}. "
            f"Supported modes for {device_type}: {', '.join(supported_modes)}."
        )
        logging.error(message)
        raise ValueError(message)
    logging.info("%s accepted awg_sequence_mode=%s", device_type, mode)
    return mode


def _validate_awg_limit(value: Any, key: str, device_type: str) -> int:
    """Validate an AWG hardware limit from synchroniser.config.

    Defaults are assigned in the synchroniser __init__; this helper is only
    called when the YAML provides an override for another AWG model.
    """

    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key} must be an integer AWG limit for {device_type}, got {value!r}."
        ) from exc
    if limit <= 0:
        raise ValueError(
            f"{key} must be a positive AWG limit for {device_type}, got {limit}."
        )
    logging.info("%s configured %s=%s", device_type, key, limit)
    return limit


class Synchroniser(ABC, ConfigurationMixin):
    """Abstract Base Class for all synchronisers.

    All synchronisers implemented in QuPyt should inherit from this class. This
    helps ensure compliance with the synchroniser API.

    Synchronisers handle the interplay of all other devices during
    one measurement step. To do this they need a pulse sequence they are
    supposed to play during the measurement. This pulse sequence is read
    from a yaml file in the sequence directory of the workspace
    (``sequence_<n>.yaml``, one per pulse sequence step). The specifics of how a pulse sequence is played
    depend on the synchroniser (Tektronix AWG, PulseStreamer, ...) and have
    to be dealt with in the implementation of this class for the
    synchroniser in question.

    Subclasses register themselves under one or more names via the
    ``synchroniser_types`` class keyword and are created with
    :meth:`create`.

    Notes
    -----
    Configure the synchroniser with the ``configuration`` constructor
    argument; its attributes are not meant to be set directly.

    Parameters
    ----------
    configuration : dict
        Configuration dictionary. Keys will be used
        to select setter methods from an attribute map dictionary to set
        associated values.

        Possible configuration values:
          - **address** (str): Address used to open a connection to the device.
            For VISA devices this could for example be: "TCPIP::<ipaddress>::INSTR".

        Concrete synchroniser classes may have additional configuration values.
    channel_mapping : dict
        Matches channel descriptors to ports
        on the device.

    Examples
    --------
    >>> sync = Synchroniser.create('TekAWG', {'address': 'TCPIP::ipaddress::INSTR'}, {'LASER': 1, 'READ': 2})
    """

    _registry: ClassVar[dict[str, type[Synchroniser]]] = {}
    synchroniser_types: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(
        cls, synchroniser_types: tuple[str, ...] = (), **kwargs: Any
    ) -> None:
        if not isinstance(synchroniser_types, tuple) or not all(
            isinstance(t, str) for t in synchroniser_types
        ):
            raise TypeError(
                f"{cls.__name__}: synchroniser_types must be a tuple, got {synchroniser_types!r}"
            )
        super().__init_subclass__(**kwargs)
        cls.synchroniser_types = synchroniser_types

        if len(set(synchroniser_types)) != len(synchroniser_types):
            raise TypeError(
                f"Tried to register duplicate synchroniser types by '{cls.__name__}'"
            )

        for name in synchroniser_types:
            if name in Synchroniser._registry:
                raise TypeError(
                    f"Synchroniser type {name!r} already registered by "
                    f"{Synchroniser._registry[name].__name__}"
                )
        for name in synchroniser_types:
            Synchroniser._registry[name] = cls

    def __init__(
        self, configuration: dict[str, Any] | None, channel_mapping: dict[str, Any]
    ) -> None:
        """Set up the shared attributes.

        Subclasses call this and then apply ``configuration`` with
        ``_update_from_configuration`` once their own setters are in the
        ``attribute_map``.

        Parameters
        ----------
        configuration : dict | None
            Configuration parameters (may be ``None``).
        channel_mapping : dict
            Matches channel descriptors to ports on the device.
        """
        self.address: str
        self.channel_mapping = channel_mapping
        self.initial_configuration_dict = configuration
        self.attribute_map: UpdateConfigurationType = {"address": self._set_address}

    def _set_address(self, address: str) -> None:
        self.address = address

    @classmethod
    def create(
        cls,
        synchroniser_type: str,
        configuration: dict[str, Any] | None,
        channel_mapping: dict[str, Any],
    ) -> Synchroniser:
        """Create and configure a registered synchroniser.

        Parameters
        ----------
        synchroniser_type : str
            Registered name, e.g. ``'TekAWG'``.
        configuration : dict | None
            Configuration parameters for the synchroniser.
        channel_mapping : dict
            Matches channel descriptors to ports on the device.

        Returns
        -------
        Synchroniser
            Instance of the requested synchroniser.

        Raises
        ------
        UnknownSynchroniserError
            if no synchroniser is registered under that name.
        """
        try:
            synchroniser_cls = cls._registry[synchroniser_type]
        except KeyError:
            logger.error(
                "Could not create synchroniser: unknown type %r (available: %s)",
                synchroniser_type,
                cls.available(),
            )
            raise UnknownSynchroniserError(synchroniser_type, cls.available()) from None
        return synchroniser_cls(configuration, channel_mapping)

    @classmethod
    def available(cls) -> list[str]:
        """Return all registered synchroniser names, sorted.

        Returns
        -------
        list[str]
            All registered synchroniser names, sorted.
        """
        return sorted(cls._registry)

    @abstractmethod
    def open(self) -> None:
        """Establish the connection to the :class:`Synchroniser` and run the configuration.

        Called once per measurement, before the first load_sequence.
        """

    @abstractmethod
    def load_sequence(self, ps_yaml_file: Path) -> None:
        """Load the YAML formatted pulse sequence ``ps_yaml_file`` (for the current pulse sequence step, ``sequence_<n>.yaml`` in the sequence directory), translate it to the synchroniser's format and upload it.

        Parameters
        ----------
        ps_yaml_file : Path
            Path of the pulse sequence YAML file.
        """

    @abstractmethod
    def run(self) -> None:
        """Activates all output channels on the devices and starts playing.

        This command does not yet start the measurement sequence. Sometimes
        this step is needed to avoid slow ramp up times and undesired pulses
        some synchronisers emit when they first start. For some synchronisers
        this step is unnecessary. However, it still needs to be implemented for
        compatibility reasons.
        """

    @abstractmethod
    def trigger(self) -> None:
        """Send a command to the synchroniser to play the measurement sequence once.

        Note that this may include however many readouts or repetitions as
        desired per measurement burst.
        """

    @abstractmethod
    def stop(self) -> None:
        """Deactivates all outputs of the synchroniser and stops the execution of the pulse sequence."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection to the synchroniser.

        Called once at the end, also after a failure. Must be safe to call if
        open() failed or never ran.
        """
