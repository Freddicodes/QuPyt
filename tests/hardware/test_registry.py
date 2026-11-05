"""Registration mechanics shared by the three hardware registries (Sensor, Synchroniser, Device).

Each family has its own copy of ``__init_subclass__`` / ``create`` /
``available``, so every test here runs against all three. Family-specific
behaviour (exact name lists, SDK import errors, constructor arguments) is
tested in the per-family files.
"""

import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from qupyt.hardware.device import Device
from qupyt.hardware.sensor import Sensor
from qupyt.hardware.synchroniser import Synchroniser
from qupyt.mixins import (
    UnknownDeviceError,
    UnknownHardwareTypeError,
    UnknownSensorError,
    UnknownSynchroniserError,
)


class _DummySensor(Sensor):
    def open(self) -> None:
        pass

    def acquire_data(self, synchroniser: Any = None) -> np.ndarray:
        return np.zeros((1, 1))

    def close(self) -> None:
        pass


class _DummySynchroniser(Synchroniser):
    def __init__(
        self, configuration: dict[str, Any], channel_mapping: dict[str, Any]
    ) -> None:
        super().__init__(configuration, channel_mapping)
        self.configuration = configuration

    def load_sequence(self, ps_yaml_file: Path) -> None:
        pass

    def run(self) -> None:
        pass

    def open(self) -> None:
        pass

    def trigger(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class _DummyDevice(Device):
    def __init__(self, address: str, configuration: dict[str, Any]) -> None:
        super().__init__(configuration)
        self.address = address

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class Family:
    base: type
    keyword: str  # class keyword used to register, e.g. sensor_types=(...)
    dummy: type  # concrete, unregistered subclass to derive test classes from
    create: Callable[[str], Any]
    error: type[UnknownHardwareTypeError]
    existing_name: str  # a name registered by the package
    existing_cls: str  # class that registered it

    def define(self, *names: str, base: type | None = None) -> type:
        """Define (and thereby register) a subclass under ``names``."""
        return types.new_class(
            "TestHardware",
            (base or self.dummy,),
            {self.keyword: names} if names else {},
        )


FAMILIES = [
    Family(
        Sensor,
        "sensor_types",
        _DummySensor,
        lambda name: Sensor.create(name, {}),
        UnknownSensorError,
        "MockCam",
        "MockCam",
    ),
    Family(
        Synchroniser,
        "synchroniser_types",
        _DummySynchroniser,
        lambda name: Synchroniser.create(name, {}, {}),
        UnknownSynchroniserError,
        "MockSynchroniser",
        "MockGenerator",
    ),
    Family(
        Device,
        "device_types",
        _DummyDevice,
        lambda name: Device.create(name, "some-address", {}),
        UnknownDeviceError,
        "Mock",
        "MockSignalSource",
    ),
]


@pytest.fixture(params=FAMILIES, ids=lambda f: f.base.__name__)
def family(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """One registry family, with a private copy of its registry so test classes don't leak."""
    fam = request.param
    monkeypatch.setattr(fam.base, "_registry", dict(fam.base._registry))
    return fam


# --- unknown names ---------------------------------------------------------------


def test_unknown_name_raises_the_family_error(family: Any) -> None:
    with pytest.raises(family.error) as exc_info:
        family.create("NotRegistered")
    assert exc_info.value.value == "NotRegistered"
    assert exc_info.value.valids == family.base.available()
    assert family.existing_name in str(exc_info.value)


def test_names_are_case_sensitive(family: Any) -> None:
    with pytest.raises(family.error):
        family.create(family.existing_name.lower())


# --- registering -------------------------------------------------------------------


def test_new_subclass_registers_itself(family: Any) -> None:
    cls = family.define("TestHardware")
    assert "TestHardware" in family.base.available()
    assert isinstance(family.create("TestHardware"), cls)


def test_one_class_can_register_several_names(family: Any) -> None:
    cls = family.define("A", "B")
    assert getattr(cls, family.keyword) == ("A", "B")
    assert type(family.create("A")) is cls
    assert type(family.create("B")) is cls


def test_subclass_without_names_is_not_registered(family: Any) -> None:
    before = family.base.available()
    family.define()
    assert family.base.available() == before


def test_duplicate_within_one_class_raises(family: Any) -> None:
    with pytest.raises(TypeError, match="Tried to register duplicate"):
        family.define("A", "A")


def test_name_registered_by_another_class_raises(family: Any) -> None:
    with pytest.raises(
        TypeError,
        match=f"'{family.existing_name}' already registered by {family.existing_cls}",
    ):
        family.define(family.existing_name)


def test_failed_registration_leaves_registry_unchanged(family: Any) -> None:
    before = family.base.available()
    with pytest.raises(TypeError):
        family.define("Fresh", family.existing_name)
    assert family.base.available() == before


def test_missing_abstract_methods_fail_on_create(family: Any) -> None:
    # Registration happens at class definition, the ABC check at instantiation.
    family.define("Incomplete", base=family.base)
    assert "Incomplete" in family.base.available()
    with pytest.raises(TypeError, match="abstract"):
        family.create("Incomplete")
