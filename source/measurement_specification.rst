Measurement Specification using YAML Files
==========================================

While you can access the features QuPyt offers a number of ways, the recommended one is to use the qupyt CLI, and specify your measurement
using yaml files. 

When you start QuPyt by typing ``qupyt`` on your command line, QuPyt will perform some setup operations if necessary.
Afterwards it will start to observe the ``waiting_room`` folder inside its workspace (``~/.qupyt/waiting_room/`` by default,
see below) to check for any new files created or moved there. If it detects new yaml files, it loads and parses them for instructions
on the next measurement to perform. How these yaml files are created is entirely up to the end user. We provide
some sample files that should be sufficient to configure your own measurement. 

Workspace and Output Directories
--------------------------------

QuPyt uses two directories:

- The **workspace** (input directory) holds the ``waiting_room`` folder that QuPyt watches,
  the generated pulse sequences (``sequences``), the log files (``log``) and ``status.txt``.
  It is chosen in this order:

  1. the ``--in-dir`` / ``-in`` command line option,
  2. the ``QUPYT_IN_DIR`` environment variable,
  3. ``~/.qupyt``.

- The **output directory** receives the measurement data (``.npy``) and a copy of the
  measurement parameters (``.yaml``). It is chosen in this order:

  1. the ``output_dir`` key in the measurement YAML file (applies to that measurement only),
  2. the ``--out-dir`` / ``-out`` command line option,
  3. the ``QUPYT_OUT_DIR`` environment variable,
  4. the directory QuPyt was started in.

Both directories are created if they do not exist. ``~`` is expanded and relative paths are
resolved against the directory QuPyt was started in.

.. code-block:: console

   qupyt --in-dir ~/qupyt_workspace --out-dir ~/data/2026-11

.. code-block:: yaml

   output_dir: '~/data/odmr_run_3'

Synchroniser Upload
-------------------

By default, QuPyt uploads the generated pulse sequence to the configured synchroniser before every pulse sequence step. To reuse a sequence that is already loaded on the synchroniser, set ``upload`` to ``false`` in the ``synchroniser`` section of the measurement YAML:

.. code-block:: yaml

   synchroniser:
     type: 'MockSynchroniser'
     upload: false
     config:
       address: 'TCPIP::some::INSTR'
     channel_mapping:
       LASER: 5
       READ: 1

Devices
-------

Devices are all parts of the setup that are neither the sensor nor the
synchroniser, e.g. microwave sources or power supplies. They are listed in two
sections of the measurement YAML, ``static_devices`` and ``dynamic_devices``.
Both have the same structure: a free name per device (it has to be unique),
and for each device its ``device_type``, its ``address`` and its ``config``:

.. code-block:: yaml

   static_devices:
     mw_source:                        # free name, unique
       device_type: 'SMB'              # see the table below
       address: 'TCPIP::192.168.0.10::INSTR'
       config:
         frequency: 2.87e9
         amplitude: -10
     psu:
       device_type: 'KonradKD3000'
       address: '/dev/tty.usbserial-1'
       config:
         output: 'on'

   dynamic_steps: 101
   dynamic_devices:
     sweep_source:
       device_type: 'SMB'
       address: 'TCPIP::192.168.0.11::INSTR'
       config:
         frequency: [2.80e9, 2.95e9]   # swept over the dynamic steps
         amplitude: ['channel_1', [-10, -10]]

Leave a section empty (``static_devices: {}``) if it is not needed. An unknown
``device_type`` stops the measurement with an error that lists all valid names.

Channels
^^^^^^^^

Every value can be given for a specific channel. Without a channel,
``channel_1`` is used:

.. code-block:: yaml

   frequency: 2.87e9                        # channel_1
   frequency: ['channel_2', 2.87e9]         # one channel
   amplitude:                               # several channels
   - ['channel_1', 1]
   - ['channel_2', 3]

Static and dynamic devices
^^^^^^^^^^^^^^^^^^^^^^^^^^

**Static devices** are set once at the start of a measurement. Only the keys
listed in ``config`` are sent to the device (power supplies are the exception,
see below).

**Dynamic devices** are set again for every dynamic step, so a measurement can
sweep e.g. a microwave frequency. The number of steps is the top-level
``dynamic_steps``. Every key in ``config`` describes the values over all steps:

- two values ``[start, stop]`` are a linear sweep from ``start`` to ``stop``
  in ``dynamic_steps`` steps (``[1, 1]`` keeps a value constant),
- a list with exactly ``dynamic_steps`` values is used as is,
- any other length is an error.

Only numeric values can be swept. Settings like ``gating`` or a power
supply's ``output`` therefore belong to ``static_devices``.

Devices stay open between measurements that are queued in the waiting room: a
device with the same name and address is reused and gets the new
configuration. A device that is no longer listed (or has a new address) is
closed. When the waiting room is empty, all devices are closed.

Device types
^^^^^^^^^^^^

Frequencies are given in Hz for all devices. Amplitudes are in the unit of the
respective device, see the notes.

.. list-table::
   :header-rows: 1
   :widths: 16 20 28 36

   * - ``device_type``
     - Device
     - Config keys
     - Notes
   * - ``Mock``
     - Mock signal source
     - ``frequency``, ``amplitude``, ``phase``
     - No hardware, only logs the values. For testing configurations.
   * - ``SRS``
     - Stanford Research Systems (VISA)
     - ``frequency``, ``amplitude``
     - Channel 1 only.
   * - ``SMA``, ``SMB``
     - Rohde & Schwarz SMA / SMB (VISA)
     - ``frequency``, ``amplitude``, ``slist_frequencies``,
       ``slist_amplitudes``
     - Amplitude in dBm. The two ``slist_*`` lists configure a frequency
       list; it is uploaded once both lists are set and have the same length.
   * - ``Rigol``
     - Rigol DG1000Z (VISA)
     - ``frequency``, ``amplitude``, ``phase``, ``gating``,
       ``triggered_n_cycles``
     - Channels 1 and 2. Amplitude in V. ``phase``: burst phase,
       -360 to 360 degrees. ``gating``: ``'off'`` or ``'gate'``.
       ``triggered_n_cycles``: integer ≥ 1, every external trigger produces
       that many periods.
   * - ``TekAFG``
     - Tektronix AFG (VISA)
     - ``frequency``, ``amplitude``, ``phase``, ``gating``
     - Channels 1 and 2. Amplitude in V. ``phase``: 0 to 360 degrees.
       ``gating``: ``'off'`` or ``'gate'``, channel 1 only.
   * - ``TekAWG``
     - Tektronix AWG (VISA)
     - (none)
     - Only opens the connection; ``frequency`` and ``amplitude`` are not
       supported yet. Use the AWG as a synchroniser for now.
   * - ``WindFreak``
     - WindFreak SynthHD via the ``windfreak`` package
     - ``frequency``, ``amplitude``, ``output_on_off``
     - Needs ``pip install qupyt[windfreak]``. Channels are
       ``channel_0`` and ``channel_1``. ``output_on_off``: ``true`` /
       ``false``.
   * - ``WindFreakHDM``
     - WindFreak SynthHD (serial)
     - ``frequency``, ``amplitude``, ``power_level``, ``output_on_off``
     - Amplitude as device setting 0 to 63.
   * - ``WindFreakSNV``
     - WindFreak SynthNV (serial)
     - ``frequency``, ``amplitude``, ``power_level``, ``output_on_off``
     - Single channel. Amplitude as device setting 0 to 63.
   * - ``WindFreakSHDMini``
     - WindFreak SynthHD mini (serial)
     - ``frequency``, ``amplitude``, ``power_level``, ``output_on_off``
     - Amplitude in dBm, -13 to 20.
   * - ``KonradKD3000``
     - Konrad power supply (serial)
     - ``output``
     - ``'on'`` or ``'off'``. If ``output`` is missing, the output is switched
       **off**: the power supply always matches the configuration. Quote the
       value: unquoted ``on`` / ``off`` are YAML booleans (they work too).
       Static devices only.

The complete list of registered names can also be printed with
``python -c "from qupyt.hardware.device import Device; print(Device.available())"``.
For the classes behind each name, see :doc:`api_reference/devices`.
