"""
Synchroniser registry: the names measurement YAML files use, how create()
passes its arguments, and driver import errors. Registration mechanics are
tested in test_registry.py.
"""
import sys

import pytest

from qupyt.hardware.synchroniser import Synchroniser
from qupyt.hardware.synchroniser.mock import MockGenerator

# The names used in the "synchroniser: type:" field of measurement YAML files.
EXPECTED_SYNCHRONISERS = ["MockSynchroniser", "PulseBlaster", "SwabInstPS", "TekAWG"]


# --- registered synchronisers -------------------------------------------------

def test_all_synchronisers_available():
    # Exact list (and therefore sorted): a missing or renamed registration
    # would break existing measurement YAML files.
    assert Synchroniser.available() == EXPECTED_SYNCHRONISERS


def test_create_mock_passes_channel_mapping():
    sync = Synchroniser.create("MockSynchroniser", {}, {"LASER": 1})
    assert isinstance(sync, MockGenerator)
    assert sync.channel_mapping == {"LASER": 1}


def test_create_mock_applies_configuration():
    # "awg_max_step_repeat" is added to the base attribute_map by MockGenerator.
    sync = Synchroniser.create("MockSynchroniser", {"awg_max_step_repeat": 5}, {})
    assert sync.awg_max_step_repeat == 5


def test_create_mock_rejects_unknown_configuration_key():
    with pytest.raises(KeyError, match="Unknown attribute: not_a_key"):
        Synchroniser.create("MockSynchroniser", {"not_a_key": 1}, {})


def test_create_pulseblaster_without_spinapi_raises_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "qupyt.hardware.wrappers.spinapi_adapted", None)

    with pytest.raises(ImportError, match="PulseBlaster needs the SpinCore spinapi driver") as exc_info:
        Synchroniser.create("PulseBlaster", {}, {})

    assert isinstance(exc_info.value.__cause__, ImportError)


def test_create_pstreamer_without_pulsestreamer_raises_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pulsestreamer", None)

    with pytest.raises(ImportError, match="PStreamer needs package 'pulsestreamer'") as exc_info:
        Synchroniser.create("SwabInstPS", {}, {})

    assert isinstance(exc_info.value.__cause__, ImportError)
