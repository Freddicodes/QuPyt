# Copyright (c) 2023 SpinCore Technologies, Inc.
# http://www.spincore.com
#
# This software is provided 'as-is', without any express or implied warranty.
# In no event will the authors be held liable for any damages arising from the
# use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
# claim that you wrote the original software. If you use this software in a
# product, an acknowledgement in the product documentation would be appreciated
# but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
# misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.

# Targets SpinAPI version 20171214 (headers: spinapi.h, pulseblaster.h, dds.h,
# radioprocessor.h, spinpts.h), as shipped in SpinCore_API_20171214_Universal.exe.
# The online reference linked for this release (2013-09-25) predates it and
# lacks some functions (e.g. pb_set_clock, pb_get_last_error, pb_select_dds).

# The following version of the file is NOT THE ORIGINAL version.
# It has been altered subject to the original license above.
# The changes made consist of formatting, type hints, a cross-platform library
# loader, corrected ctypes signatures and bindings for the 20171214 API.

"""ctypes bindings for the SpinCore SpinAPI library.

The shared library is searched for in this order:

1. The path in the ``SPINAPI_LIBRARY`` environment variable.
2. Windows: ``spinapi64`` and ``spinapi`` (DLL search path).
   Linux: ``libspinapi.so`` on the loader path, then the default
   ``make install`` location ``/opt/spinapi/lib/libspinapi.so``.

If the library cannot be loaded, importing this module raises ``ImportError``.
"""

import ctypes
import ctypes.util
import os
import sys
from collections.abc import Sequence
from typing import Any

# --- library loading --------------------------------------------------------


def _library_candidates() -> list[str]:
    candidates: list[str] = []
    env_path = os.environ.get("SPINAPI_LIBRARY")
    if env_path:
        candidates.append(env_path)
    if sys.platform.startswith("win"):
        candidates += ["spinapi64", "spinapi"]
    else:
        found = ctypes.util.find_library("spinapi")
        if found:
            candidates.append(found)
        candidates += ["libspinapi.so", "/opt/spinapi/lib/libspinapi.so"]
    return candidates


def _load_library() -> ctypes.CDLL:
    errors: list[str] = []
    last_error: OSError | None = None
    for candidate in _library_candidates():
        try:
            return ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            last_error = exc
    raise ImportError(
        "Failed to load the SpinCore spinapi library. Tried:\n  "
        + "\n  ".join(errors)
        + "\nSet SPINAPI_LIBRARY to the full path of the library to override."
    ) from last_error


spinapi = _load_library()


# Names of all C functions declared below, in header order.
DECLARED_FUNCTIONS: list[str] = []


def _declare(name: str, restype: Any, *argtypes: Any) -> None:
    """Set the C signature of ``name`` if the loaded library exports it.

    Symbols missing from the loaded library are skipped; calling the Python
    wrapper of such a function raises ``AttributeError``.
    """
    DECLARED_FUNCTIONS.append(name)
    try:
        func = getattr(spinapi, name)
    except AttributeError:
        return
    func.restype = restype
    func.argtypes = argtypes


# --- constants --------------------------------------------------------------


def enum(**enums: Any) -> type:
    return type("Enum", (), enums)


MAX_NUM_BOARDS = 32
PARAM_ERROR = -99

# Defines for start_programming
PULSE_PROGRAM = 0
FREQ_REGS = 1
PHASE_REGS = 2
TX_PHASE_REGS = 2
PHASE_REGS_1 = 2
RX_PHASE_REGS = 3
PHASE_REGS_0 = 3
# These are names used by RadioProcessor
COS_PHASE_REGS = 51
SIN_PHASE_REGS = 50

# Units of time (base unit ns)
ns = 1.0
us = 1000.0
ms = 1000000.0

# Units of frequency (base unit MHz)
MHz = 1.0
kHz = 0.001
Hz = 0.000001

# Defines for status bits
STATUS_STOPPED = 1
STATUS_RESET = 2
STATUS_RUNNING = 4
STATUS_WAITING = 8
STATUS_SCANNING = 16

# Instruction enum
Inst = enum(
    CONTINUE=0,
    STOP=1,
    LOOP=2,
    END_LOOP=3,
    JSR=4,
    RTS=5,
    BRANCH=6,
    LONG_DELAY=7,
    WAIT=8,
    RTI=9,
)

CONTINUE = 0
STOP = 1
LOOP = 2
END_LOOP = 3
JSR = 4
RTS = 5
BRANCH = 6
LONG_DELAY = 7
WAIT = 8
RTI = 9

# Short pulse feature (flag bits 21-23)
ALL_FLAGS_ON = 0x1FFFFF
ONE_PERIOD = 0x200000
TWO_PERIOD = 0x400000
THREE_PERIOD = 0x600000
FOUR_PERIOD = 0x800000
FIVE_PERIOD = 0xA00000
SIX_PERIOD = 0xC00000
ON = 0xE00000

REG_SHORTPULSE_DISABLE = 0x06
REG_START_ADDRESS = 0x07
REG_DEFAULT_FLAGS = 0x08

# DDS boards only (defined under PBDDS in dds.h)
PHASE_RESET = 0x200

# For specifying which device in pb_dds_load
DEVICE_SHAPE = 0x099000
DEVICE_DDS = 0x099001

# Defines for enabling analog output
ANALOG_ON = 1
ANALOG_OFF = 0
TX_ANALOG_ON = 1
TX_ANALOG_OFF = 0
RX_ANALOG_ON = 1
RX_ANALOG_OFF = 0

# RadioProcessor control word defines
TRIGGER = 0x0001
PCI_READ = 0x0002
BYPASS_AVERAGE = 0x0004
NARROW_BW = 0x0008
FORCE_AVG = 0x0010
BNC0_CLK = 0x0020
DO_ZERO = 0x0040
BYPASS_CIC = 0x0080
BYPASS_FIR = 0x0100
BYPASS_MULT = 0x0200
SELECT_AUX_DDS = 0x0400
DDS_DIRECT = 0x0800
SELECT_INTERNAL_DDS = 0x1000
DAC_FEEDTHROUGH = 0x2000
OVERFLOW_RESET = 0x4000
RAM_DIRECT = 0x8000 | BYPASS_CIC | BYPASS_FIR | BYPASS_MULT

# SpinPTS error codes
PHASE_INVALID = 0x100
FREQ_ORANGE = 0x101
DWRITE_FAIL = 0x200
DEVICE_OPEN_FAIL = 0x201
NO_DEVICE_FOUND = 0x202


class PB_OVERFLOW_STRUCT(ctypes.Structure):
    """Onboard overflow counters of RadioProcessor boards (saturate at 65535)."""

    _fields_ = [
        ("adc", ctypes.c_int),
        ("cic", ctypes.c_int),
        ("fir", ctypes.c_int),
        ("average", ctypes.c_int),
    ]


# --- C signatures -----------------------------------------------------------

_int = ctypes.c_int
_uint = ctypes.c_uint
_int64 = ctypes.c_longlong
_double = ctypes.c_double
_float = ctypes.c_float
_char_p = ctypes.c_char_p
_int_p = ctypes.POINTER(ctypes.c_int)
_short_p = ctypes.POINTER(ctypes.c_short)
_double_p = ctypes.POINTER(ctypes.c_double)
_float_p = ctypes.POINTER(ctypes.c_float)

# spinapi.h
_declare("pb_count_boards", _int)
_declare("pb_select_board", _int, _int)
_declare("pb_init", _int)
_declare("pb_core_clock", None, _double)
_declare("pb_set_clock", None, _double)
_declare("pb_close", _int)
_declare("pb_start_programming", _int, _int)
_declare("pb_stop_programming", _int)
_declare("pb_start", _int)
_declare("pb_stop", _int)
_declare("pb_reset", _int)
_declare("pb_outp", _int, _uint, ctypes.c_ubyte)
_declare("pb_inp", ctypes.c_char, _uint)
_declare("pb_outw", _int, _uint, _uint)
_declare("pb_inw", _uint, _uint)
_declare("pb_set_ISA_address", None, _int)
_declare("pb_get_rounded_value", _double)
_declare("pb_read_status", _int)
_declare("pb_status_message", _char_p)
_declare("pb_get_version", _char_p)
_declare("pb_get_error", _char_p)
_declare("pb_get_last_error", _int, _char_p, _uint)
_declare("pb_get_firmware_id", _int)
_declare("pb_sleep_ms", None, _int)
_declare("pb_set_debug", _int, _int)

# pulseblaster.h
_declare("pb_4C_inst", _int, _int, _double)
_declare("pb_4C_stop", _int)
_declare("pb_inst_pbonly", _int, _uint, _int, _int, _double)
_declare("pb_inst_pbonly64", _int, _int64, _int, _int, _double)
_declare("pb_inst_direct", _int, _int_p, _int, _int, _int)
_declare("pb_bypass_FF_fix", None, _int)
_declare("pb_inst_hs24", _int, _char_p, _double)
_declare("pb_inst_hs8", _int, _char_p, _double)
_declare("pb_select_core", _int, _uint)
_declare("pb_set_pulse_regs", _int, _uint, _double, _double, _double)

# dds.h
_declare("pb_set_freq", _int, _double)
_declare("pb_set_phase", _int, _double)
_declare("pb_inst_tworf", _int, _int, _int, _int, _int, _int, _int, _int, _int, _double)
_declare("pb_inst_onerf", _int, _int, _int, _int, _int, _int, _int, _double)
_declare(
    "pb_inst_dds2",
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq0, phase0, amp0, dds_en0, phase_reset0
    _int,
    _int,
    _int,
    _int,
    _int,  # freq1, phase1, amp1, dds_en1, phase_reset1
    _int,
    _int,
    _int,  # flags, inst, inst_data
    _double,  # length
)
_declare(
    "pb_inst_dds2_shape",
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq0, phase0, amp0, use_shape0, dds_en0, phase_reset0
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq1, phase1, amp1, use_shape1, dds_en1, phase_reset1
    _int,
    _int,
    _int,  # flags, inst, inst_data
    _double,  # length
)
_declare("pb_set_shape_defaults", _int)
_declare("pb_select_dds", _int, _int)
_declare("pb_dds_load", _int, _float_p, _int)
_declare("pb_dds_set_envelope_freq", None, _float, _int)
_declare("pb_set_amp", _int, _float, _int)

# radioprocessor.h
_declare("pb_set_defaults", _int)
_declare(
    "pb_inst_radio",
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq, cos/sin/tx phase, tx_enable, phase_reset, trigger_scan
    _int,
    _int,
    _int,  # flags, inst, inst_data
    _double,  # length
)
_declare(
    "pb_inst_radio_shape",
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq, cos/sin/tx phase, tx_enable, phase_reset, trigger_scan
    _int,
    _int,  # use_shape, amp
    _int,
    _int,
    _int,  # flags, inst, inst_data
    _double,  # length
)
_declare(
    "pb_inst_radio_shape_cyclops",
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,
    _int,  # freq, cos/sin/tx phase, tx_enable, phase_reset, trigger_scan
    _int,
    _int,  # use_shape, amp
    _int,
    _int,
    _int,  # real_add_sub, imag_add_sub, channel_swap
    _int,
    _int,
    _int,  # flags, inst, inst_data
    _double,  # length
)
_declare("pb_set_num_points", _int, _int)
_declare("pb_set_scan_segments", _int, _int)
_declare("pb_scan_count", _int, _int)
_declare("pb_overflow", _int, _int, ctypes.POINTER(PB_OVERFLOW_STRUCT))
_declare("pb_get_data", _int, _int, _int_p, _int_p)
_declare("pb_get_data_direct", _int, _int, _short_p)
_declare("pb_write_ascii", _int, _char_p, _int, _float, _int_p, _int_p)
_declare("pb_write_ascii_verbose", _int, _char_p, _int, _float, _float, _int_p, _int_p)
_declare("pb_write_jcamp", _int, _char_p, _int, _float, _float, _int_p, _int_p)
_declare("pb_write_felix", _int, _char_p, _char_p, _int, _float, _float, _int_p, _int_p)
_declare("pb_setup_filters", _int, _double, _int, _int)
_declare("pb_setup_cic", _int, _int, _int, _int, _int)
_declare("pb_load_coef_file", _int, _int_p, _char_p, _int)
_declare("pb_setup_fir", _int, _int, _int_p, _int, _int)
_declare("pb_set_radio_control", _int, _uint)
_declare("pb_unset_radio_control", _int, _uint)
_declare("pb_set_radio_hw", _int, _int, _int)
_declare("pb_set_isr", _int, _int, _uint)
_declare("pb_set_irq_enable_mask", _int, ctypes.c_char)
_declare("pb_set_irq_immediate_mask", _int, ctypes.c_char)
_declare("pb_generate_interrupt", _int, ctypes.c_char)
_declare("pb_write_register", _int, _uint, _uint)
_declare("pb_zero_ram", _int)
_declare("pb_adc_zero", _int, _int)
_declare("pb_fft", _int, _int, _int_p, _int_p, _double_p, _double_p, _double_p)
_declare("pb_fft_find_resonance", _double, _int, _double, _double, _int_p, _int_p)

# spinpts.h
_declare("set_pts", _int, _double, _int, _int, _int, _int, _double, _int)
_declare("set_pts_ex", _int, _int, _double, _int, _int, _int, _int, _double, _int)
_declare("spinpts_get_error", _char_p)
_declare("spinpts_get_version", _char_p)


# --- helpers ----------------------------------------------------------------


def _decode(value: bytes | None, operation: str) -> str:
    if value is None:
        raise RuntimeError(f"{operation} returned a null string pointer")
    return value.decode("utf-8", errors="replace")


def _encode(value: str) -> bytes:
    return value.encode()


def _int_array(values: Sequence[int], length: int | None = None) -> Any:
    length = len(values) if length is None else length
    return (ctypes.c_int * length)(*values)


# --- general (spinapi.h) ----------------------------------------------------


def pb_get_version() -> str:
    """Return the library version (YYYYMMDD)."""
    return _decode(spinapi.pb_get_version(), "pb_get_version")


def pb_get_error() -> str:
    """Return the most recent error string (deprecated in SpinAPI, still exported)."""
    return _decode(spinapi.pb_get_error(), "pb_get_error")


def pb_get_last_error(size: int = 512) -> str:
    """Return the most recent error string via the non-deprecated API."""
    buffer = ctypes.create_string_buffer(size + 1)
    spinapi.pb_get_last_error(buffer, size)
    return _decode(buffer.value, "pb_get_last_error")


def pb_count_boards() -> int:
    """Return the number of boards detected in the system (-1 on error)."""
    return spinapi.pb_count_boards()


def pb_init() -> int:
    """Initialize currently selected board."""
    return spinapi.pb_init()


def pb_set_debug(debug: int) -> int:
    """Enable (1) or disable (0) writing a debug log (log.txt)."""
    return spinapi.pb_set_debug(int(debug))


def pb_select_board(board_number: int) -> int:
    """Select a specific board number (counting starts at 0)."""
    return spinapi.pb_select_board(int(board_number))


def pb_core_clock(clock: float) -> None:
    """Tell the library the core clock frequency in MHz. Returns nothing."""
    spinapi.pb_core_clock(clock)


def pb_set_clock(clock: float) -> None:
    """Alias of pb_core_clock."""
    spinapi.pb_set_clock(clock)


def pb_start_programming(target: int) -> int:
    return spinapi.pb_start_programming(int(target))


def pb_stop_programming() -> int:
    return spinapi.pb_stop_programming()


def pb_start() -> int:
    return spinapi.pb_start()


def pb_stop() -> int:
    return spinapi.pb_stop()


def pb_reset() -> int:
    return spinapi.pb_reset()


def pb_close() -> int:
    return spinapi.pb_close()


def pb_read_status() -> int:
    return spinapi.pb_read_status()


def pb_status_message() -> str:
    """Return the board status message."""
    return _decode(spinapi.pb_status_message(), "pb_status_message")


def pb_get_firmware_id() -> int:
    return spinapi.pb_get_firmware_id()


def pb_sleep_ms(milliseconds: int) -> None:
    spinapi.pb_sleep_ms(int(milliseconds))


def pb_get_rounded_value() -> float:
    """Return the last rounded value (declared in spinapi.h, not exported by the 20171214 DLL)."""
    return spinapi.pb_get_rounded_value()


def pb_outp(address: int, data: int) -> int:
    return spinapi.pb_outp(int(address), int(data))


def pb_inp(address: int) -> int:
    return ord(spinapi.pb_inp(int(address)))


def pb_outw(address: int, data: int) -> int:
    return spinapi.pb_outw(int(address), int(data))


def pb_inw(address: int) -> int:
    return spinapi.pb_inw(int(address))


def pb_set_ISA_address(address: int) -> None:
    spinapi.pb_set_ISA_address(int(address))


# --- PulseBlaster (pulseblaster.h) ------------------------------------------


def pb_inst_pbonly(flags: int, inst: int, inst_data: int, length: float) -> int:
    """Program one instruction (32-bit flags). Length in ns."""
    return spinapi.pb_inst_pbonly(int(flags), int(inst), int(inst_data), length)


def pb_inst_pbonly64(flags: int, inst: int, inst_data: int, length: float) -> int:
    """Program one instruction (64-bit flags). Length in ns."""
    return spinapi.pb_inst_pbonly64(int(flags), int(inst), int(inst_data), length)


def pb_inst_direct(
    flags: Sequence[int], inst: int, inst_data_direct: int, length: int
) -> int:
    return spinapi.pb_inst_direct(
        _int_array(flags), int(inst), int(inst_data_direct), int(length)
    )


def pb_bypass_FF_fix(option: int) -> None:
    spinapi.pb_bypass_FF_fix(int(option))


def pb_inst_hs24(flags: str, length: float) -> int:
    return spinapi.pb_inst_hs24(_encode(flags), length)


def pb_inst_hs8(flags: str, length: float) -> int:
    return spinapi.pb_inst_hs8(_encode(flags), length)


def pb_select_core(core_sel: int) -> int:
    return spinapi.pb_select_core(int(core_sel))


def pb_4C_inst(flag: int, length: float) -> int:
    return spinapi.pb_4C_inst(int(flag), length)


def pb_4C_stop() -> int:
    return spinapi.pb_4C_stop()


def pb_set_pulse_regs(
    channel: int, period: float, clock_high: float, offset: float
) -> int:
    return spinapi.pb_set_pulse_regs(int(channel), period, clock_high, offset)


# --- DDS (dds.h) ------------------------------------------------------------


def pb_set_freq(freq: float) -> int:
    return spinapi.pb_set_freq(freq)


def pb_set_phase(phase: float) -> int:
    return spinapi.pb_set_phase(phase)


def pb_set_amp(amp: float, addr: int) -> int:
    return spinapi.pb_set_amp(amp, int(addr))


def pb_inst_tworf(*args: Any) -> int:
    return spinapi.pb_inst_tworf(*args)


def pb_inst_onerf(*args: Any) -> int:
    return spinapi.pb_inst_onerf(*args)


def pb_inst_dds2(*args: Any) -> int:
    return spinapi.pb_inst_dds2(*args)


def pb_inst_dds2_shape(*args: Any) -> int:
    return spinapi.pb_inst_dds2_shape(*args)


def pb_set_shape_defaults() -> int:
    return spinapi.pb_set_shape_defaults()


def pb_select_dds(dds_num: int) -> int:
    return spinapi.pb_select_dds(int(dds_num))


def pb_dds_load(data: Sequence[float], device: int) -> int:
    c_data = (ctypes.c_float * len(data))(*data)
    return spinapi.pb_dds_load(c_data, int(device))


def pb_dds_set_envelope_freq(freq: float, n: int) -> None:
    spinapi.pb_dds_set_envelope_freq(freq, int(n))


def pb_inst_dds(
    FREQ: int,
    TX_PHASE: int,
    TX_ENABLE: int,
    PHASE_RESET: int,
    FLAGS: int,
    INST: int,
    INST_DATA: int,
    LENGTH: float,
) -> int:
    return pb_inst_radio(
        FREQ, 0, 0, TX_PHASE, TX_ENABLE, PHASE_RESET, 0, FLAGS, INST, INST_DATA, LENGTH
    )


def pb_inst_dds_shape(
    FREQ: int,
    TX_PHASE: int,
    TX_ENABLE: int,
    PHASE_RESET: int,
    USESHAPE: int,
    AMP: int,
    FLAGS: int,
    INST: int,
    INST_DATA: int,
    LENGTH: float,
) -> int:
    return pb_inst_radio_shape(
        FREQ,
        0,
        0,
        TX_PHASE,
        TX_ENABLE,
        PHASE_RESET,
        0,
        USESHAPE,
        AMP,
        FLAGS,
        INST,
        INST_DATA,
        LENGTH,
    )


# --- RadioProcessor (radioprocessor.h) --------------------------------------


def pb_set_defaults() -> int:
    """Set board defaults. Must be called before using any other board functions."""
    return spinapi.pb_set_defaults()


def pb_inst_radio(*args: Any) -> int:
    return spinapi.pb_inst_radio(*args)


def pb_inst_radio_shape(*args: Any) -> int:
    return spinapi.pb_inst_radio_shape(*args)


def pb_inst_radio_shape_cyclops(*args: Any) -> int:
    return spinapi.pb_inst_radio_shape_cyclops(*args)


def pb_set_num_points(num_points: int) -> int:
    return spinapi.pb_set_num_points(int(num_points))


def pb_set_scan_segments(num_segments: int) -> int:
    return spinapi.pb_set_scan_segments(int(num_segments))


def pb_scan_count(reset: int) -> int:
    return spinapi.pb_scan_count(int(reset))


def pb_overflow(reset: int, of: PB_OVERFLOW_STRUCT | None = None) -> int:
    """Read (and optionally reset) the overflow counters into ``of``."""
    return spinapi.pb_overflow(int(reset), ctypes.byref(of) if of is not None else None)


def pb_get_data(num_points: int) -> tuple[int, list[int], list[int]]:
    """Read ``num_points`` complex points from RAM.

    Returns ``(status, real_data, imag_data)``.
    """
    real = (ctypes.c_int * num_points)()
    imag = (ctypes.c_int * num_points)()
    status = spinapi.pb_get_data(int(num_points), real, imag)
    return status, list(real), list(imag)


def pb_get_data_direct(num_points: int) -> tuple[int, list[int]]:
    """Read ``num_points`` raw ADC samples. Returns ``(status, data)``."""
    data = (ctypes.c_short * num_points)()
    status = spinapi.pb_get_data_direct(int(num_points), data)
    return status, list(data)


def pb_write_ascii(
    fname: str,
    num_points: int,
    SW: float,
    real_data: Sequence[int],
    imag_data: Sequence[int],
) -> int:
    return spinapi.pb_write_ascii(
        _encode(fname),
        int(num_points),
        SW,
        _int_array(real_data, num_points),
        _int_array(imag_data, num_points),
    )


def pb_write_ascii_verbose(
    fname: str,
    num_points: int,
    SW: float,
    SF: float,
    real_data: Sequence[int],
    imag_data: Sequence[int],
) -> int:
    return spinapi.pb_write_ascii_verbose(
        _encode(fname),
        int(num_points),
        SW,
        SF,
        _int_array(real_data, num_points),
        _int_array(imag_data, num_points),
    )


def pb_write_jcamp(
    fname: str,
    num_points: int,
    SW: float,
    SF: float,
    real_data: Sequence[int],
    imag_data: Sequence[int],
) -> int:
    return spinapi.pb_write_jcamp(
        _encode(fname),
        int(num_points),
        SW,
        SF,
        _int_array(real_data, num_points),
        _int_array(imag_data, num_points),
    )


def pb_write_felix(
    fnameout: str,
    title_string: str,
    num_points: int,
    SW: float,
    SF: float,
    real_data: Sequence[int],
    imag_data: Sequence[int],
) -> int:
    return spinapi.pb_write_felix(
        _encode(fnameout),
        _encode(title_string),
        int(num_points),
        SW,
        SF,
        _int_array(real_data, num_points),
        _int_array(imag_data, num_points),
    )


def pb_setup_filters(spectral_width: float, scan_repetitions: int, cmd: int) -> int:
    return spinapi.pb_setup_filters(spectral_width, int(scan_repetitions), int(cmd))


def pb_setup_cic(dec_amount: int, shift_amount: int, m: int, stages: int) -> int:
    return spinapi.pb_setup_cic(int(dec_amount), int(shift_amount), int(m), int(stages))


def pb_load_coef_file(fname: str, num_coefs: int) -> tuple[int, list[int]]:
    """Load FIR coefficients from file. Returns ``(status, coefficients)``."""
    coef = (ctypes.c_int * num_coefs)()
    status = spinapi.pb_load_coef_file(coef, _encode(fname), int(num_coefs))
    return status, list(coef)


def pb_setup_fir(
    num_taps: int, coef: Sequence[int], shift_amount: int, dec_amount: int
) -> int:
    return spinapi.pb_setup_fir(
        int(num_taps), _int_array(coef), int(shift_amount), int(dec_amount)
    )


def pb_set_radio_control(control: int) -> int:
    return spinapi.pb_set_radio_control(int(control))


def pb_unset_radio_control(control: int) -> int:
    return spinapi.pb_unset_radio_control(int(control))


def pb_set_radio_hw(adc_control: int, dac_control: int) -> int:
    return spinapi.pb_set_radio_hw(int(adc_control), int(dac_control))


def pb_set_isr(irq_num: int, isr_addr: int) -> int:
    return spinapi.pb_set_isr(int(irq_num), int(isr_addr))


def pb_set_irq_enable_mask(mask: int) -> int:
    return spinapi.pb_set_irq_enable_mask(bytes([mask & 0xFF]))


def pb_set_irq_immediate_mask(mask: int) -> int:
    return spinapi.pb_set_irq_immediate_mask(bytes([mask & 0xFF]))


def pb_generate_interrupt(mask: int) -> int:
    return spinapi.pb_generate_interrupt(bytes([mask & 0xFF]))


def pb_write_register(address: int, value: int) -> int:
    return spinapi.pb_write_register(int(address), int(value))


def pb_zero_ram() -> int:
    return spinapi.pb_zero_ram()


def pb_adc_zero(set_: int) -> int:
    return spinapi.pb_adc_zero(int(set_))


def pb_fft(
    n: int, real_in: Sequence[int], imag_in: Sequence[int]
) -> tuple[int, list[float], list[float], list[float]]:
    """Return ``(status, real_out, imag_out, mag_fft)``."""
    real_out = (ctypes.c_double * n)()
    imag_out = (ctypes.c_double * n)()
    mag_fft = (ctypes.c_double * n)()
    status = spinapi.pb_fft(
        int(n),
        _int_array(real_in, n),
        _int_array(imag_in, n),
        real_out,
        imag_out,
        mag_fft,
    )
    return status, list(real_out), list(imag_out), list(mag_fft)


def pb_fft_find_resonance(
    num_points: int,
    SF: float,
    SW: float,
    real_data: Sequence[int],
    imag_data: Sequence[int],
) -> float:
    return spinapi.pb_fft_find_resonance(
        int(num_points),
        SF,
        SW,
        _int_array(real_data, num_points),
        _int_array(imag_data, num_points),
    )


def pb_get_wait_time(adc_freq: float, n_points: int, dec: int) -> float:
    """Port of the pb_get_wait_time macro: acquisition time in ms."""
    return 1000.0 * float(dec) * float(n_points) / (float(adc_freq) * 1e6)


# --- SpinPTS (spinpts.h) ----------------------------------------------------


def set_pts(
    max_freq: float,
    is160: int,
    is3200: int,
    allow_phase: int,
    no_pts: int,
    frequency: float,
    phase: int,
) -> int:
    return spinapi.set_pts(
        max_freq,
        int(is160),
        int(is3200),
        int(allow_phase),
        int(no_pts),
        frequency,
        int(phase),
    )


def set_pts_ex(
    pts_index: int,
    max_freq: float,
    is160: int,
    is3200: int,
    allow_phase: int,
    no_pts: int,
    frequency: float,
    phase: int,
) -> int:
    return spinapi.set_pts_ex(
        int(pts_index),
        max_freq,
        int(is160),
        int(is3200),
        int(allow_phase),
        int(no_pts),
        frequency,
        int(phase),
    )


def spinpts_get_error() -> str:
    return _decode(spinapi.spinpts_get_error(), "spinpts_get_error")


def spinpts_get_version() -> str:
    return _decode(spinapi.spinpts_get_version(), "spinpts_get_version")
