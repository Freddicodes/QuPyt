Measurement Flow and Data Structure
===================================

This page explains what QuPyt does with a measurement YAML file, step by step,
and what the recorded data looks like on disk. It is meant for users who want
to understand their output files, and for contributors who want to change the
measurement loop.

.. contents:: On this page
   :local:
   :depth: 2


Folders and files
-----------------

QuPyt uses two folders:

- **The folder you start QuPyt in.** Open a terminal, ``cd`` to the folder
  where you want your data, and run ``qupyt``. All measurement results are
  written here. Relative ``ps_path`` values in your YAML files are resolved
  from here as well.
- **The QuPyt home folder** (``~/.qupyt`` by default). It is created on the
  first start and contains:

  - ``waiting_room/``: put your measurement YAML files here.
  - ``sequences/``: the pulse sequences generated for the synchroniser
    (``sequence_0.yaml``, ``sequence_1.yaml``, ... and ``sequence.npz`` for an AWG).
  - ``log/``: one log file per day (``log_YYYY-MM-DD.log``).
  - ``status.txt``: contains ``ready`` or ``busy``. Other programs can read
    it to see whether QuPyt is idle.


Measurement flow
----------------

Overview::

    YAML file copied into ~/.qupyt/waiting_room/
      │
      ▼
    1. Pick up        file renamed to <name>_running_<timestamp>
      │
      ▼
    2. Prepare        pulse sequence generated, devices created and configured
      │
      ▼
    3. Measure        for each pulse-sequence step
      │                 for each dynamic step
      │                   for each average: read sensor, add to data
      ▼
    4. Finish         devices closed, <experiment>_<time>.npz + .yaml written,
                      instruction file deleted (success) or renamed to *_failed_*

1. Picking up an instruction file
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

QuPyt watches ``waiting_room/`` for new ``.yaml`` files. Files are measured one
after another, in the order they arrive. Files that are already in the waiting
room when QuPyt starts are queued too, oldest first.

When a file is picked up, ``status.txt`` is set to ``busy`` and the file is
renamed to ``<name>_running_<timestamp>``, so it is not picked up twice. If the
file cannot be read (for example because it is still being written), QuPyt
waits 0.5 s and tries once more. If it still fails, the file is skipped and
the reason is written to the log.

2. Preparing the measurement
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

#. **Pulse sequence.** QuPyt loads the Python file given in ``ps_path`` and
   calls its ``generate_sequence(params)`` function with the ``pulse_sequence:``
   block of your YAML. This function writes one ``sequence_<n>.yaml`` per
   pulse-sequence step into ``~/.qupyt/sequences/``. If it returns a
   dictionary, those values are merged into the measurement parameters.
#. **Synchroniser and sensor** are created from the ``synchroniser:`` and
   ``sensor:`` blocks.
#. **Static devices** (``static_devices:``) are configured once. They keep the
   same value for the whole measurement.
#. **Dynamic devices** (``dynamic_devices:``) get a list of values with one
   value per dynamic step. A ``[start, stop]`` pair is spread evenly over
   ``dynamic_steps`` values; a longer list must have exactly
   ``dynamic_steps`` entries.

3. The measurement loop
^^^^^^^^^^^^^^^^^^^^^^^

The loop in :func:`qupyt.measurement_logic.run_measurement.run_measurement`
has three levels. In simplified form:

.. code-block:: python

   for ps_step in range(pulse_sequence_steps):
       # load sequence_<ps_step>.yaml onto the synchroniser, start it, open the sensor
       for dynamic_step in range(dynamic_steps):
           dynamic_devices.next_dynamic_step()   # e.g. set the next MW frequency
           for avg in range(averages):
               sleep(params["sleep"])
               frames = sensor.acquire_data(synchroniser)
               data.add(frames, ps_step=ps_step, dynamic_step=dynamic_step)

The three loop sizes come from top-level keys of the measurement YAML:

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Key
     - Default
     - Meaning
   * - ``pulse_sequence_steps``
     - 1
     - Number of different pulse sequences, e.g. for a sweep of a pulse length.
   * - ``dynamic_steps``
     - 1
     - Number of values each dynamic device steps through.
   * - ``averages``
     - required
     - Number of sensor readouts per dynamic step. They are averaged.

These keys also set the size of the data array.

Every call of ``sensor.acquire_data`` triggers the synchroniser once and
returns ``number_measurements`` frames (camera images, or single values for a
DAQ). ``number_measurements`` is set in the ``sensor:`` block.

4. Finishing
^^^^^^^^^^^^

Whether the measurement succeeded or failed, QuPyt then:

#. closes the sensor and the synchroniser,
#. saves the data as ``<experiment_type>_<YYYY-mm-dd-HH-MM-SS>.npz``,
#. saves all parameters as ``<experiment_type>_<YYYY-mm-dd-HH-MM-SS>.yaml``,
#. deletes the instruction file on success, or renames it to
   ``<name>_failed_<timestamp>`` on failure.

If something goes wrong, the error is written to the log, and the ``.yaml``
contains ``measurement_status: failed``. Data recorded up to that point is
still saved. If the error happens before the data array is created (for
example because of a typo in the ``data:`` block), there is no ``.npz`` file.


The data structure
------------------

All data of one measurement is stored in a single array with these axes::

    (channel, ps_step, dynamic_step, measurement, *roi)

.. list-table::
   :header-rows: 1
   :widths: 18 32 50

   * - Axis
     - Length
     - Meaning
   * - ``channel``
     - ``data.reference_channels``
     - Reference channel, e.g. 0 = signal (MW on) and 1 = reference (MW off).
   * - ``ps_step``
     - ``pulse_sequence_steps``
     - Pulse-sequence step.
   * - ``dynamic_step``
     - ``dynamic_steps``
     - Dynamic-device step, e.g. one MW frequency.
   * - ``measurement``
     - 1 (``sum``) or ``number_measurements / reference_channels`` (``spread``)
     - Frame within one channel; see `Averaging modes`_.
   * - ``roi_0``, ``roi_1``, ...
     - the sensor's region of interest
     - ``(height, width)`` for a camera, ``(1,)`` for a DAQ or with
       ``live_compression``.

Averages are not an axis. All averages of a step are added into the same
place and then divided by their number.

How frames are sorted into channels
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The frames of one readout are dealt out to the channels in turn: frame ``k``
goes to channel ``k % reference_channels``. With 4 frames ``f0 f1 f2 f3`` and
2 channels, channel 0 gets ``f0, f2`` and channel 1 gets ``f1, f3``.

Your pulse sequence must therefore alternate its readouts in the same order
(e.g. signal, reference, signal, reference, ...). ``number_measurements`` must
be divisible by ``reference_channels``, otherwise the measurement does not
start.

Averaging modes
^^^^^^^^^^^^^^^

``data.averaging_mode`` decides what happens with the frames of one channel:

- ``sum``: the frames are **added up**. The ``measurement`` axis has length 1.
  Note that this is a sum, not a mean: the values grow with
  ``number_measurements``. Use this e.g. for ODMR, where every frame of a
  channel measures the same thing.
- ``spread``: every frame is **kept**. The ``measurement`` axis has length
  ``number_measurements / reference_channels``. Use this when the frames
  differ, e.g. for Rabi or XY8 sequences where each readout belongs to a
  different pulse length.

With the example above (channel 0 gets ``f0, f2``):

- ``sum``: ``data[0, ps, dyn, 0] = f0 + f2``
- ``spread``: ``data[0, ps, dyn, 0] = f0`` and ``data[0, ps, dyn, 1] = f2``

In both modes the saved value is the **mean over the averages** of that step.

``live_compression``
^^^^^^^^^^^^^^^^^^^^

With ``live_compression: true``, every camera frame is reduced to the mean
over all its pixels before it is stored. The ROI axes then become a single
axis of length 1. Use this when you only need the total brightness and the
full images would use too much memory.

Example shapes
^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Configuration
     - Array shape
   * - Camera, 200 × 300 ROI, ``number_measurements: 100``,
       2 channels, 10 dynamic steps, ``sum``
     - ``(2, 1, 10, 1, 200, 300)``
   * - Same, ``spread``
     - ``(2, 1, 10, 50, 200, 300)``
   * - Same, ``spread`` with ``live_compression: true``
     - ``(2, 1, 10, 50, 1)``
   * - DAQ, ``number_measurements: 1000``, 2 channels,
       1 dynamic step, ``spread``
     - ``(2, 1, 1, 500, 1)``

The ``data:`` block
^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

   data:
     averaging_mode: 'sum'     # 'sum' or 'spread' (required)
     reference_channels: 2     # default 2
     live_compression: false   # default false
     save_in_chunks: 0         # default 0 (off), see below

Any other key is an error.

Saving in chunks
^^^^^^^^^^^^^^^^

With ``save_in_chunks: n``, QuPyt also saves an extra file after every ``n``
averages: ``<experiment_type>_ch-<i>_<YYYY-mm-dd-HH-MM-SS>.npz``. Each chunk
file has the same layout as the main file but only contains the mean of
those ``n`` averages. All other steps are ``NaN``. This lets you look at
drifts during a long measurement. ``averages`` must be divisible by ``n``.


Output files
------------

The ``.npz`` file
^^^^^^^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Array
     - Content
   * - ``data``
     - The measurement array described above, as ``float64``. Steps that
       received no average (e.g. after a failure) are ``NaN``.
   * - ``averages``
     - Number of averages recorded per ``(ps_step, dynamic_step)``.
   * - ``axes``
     - The axis names, e.g.
       ``['channel', 'ps_step', 'dynamic_step', 'measurement', 'roi_0', 'roi_1']``.

The ``.yaml`` file
^^^^^^^^^^^^^^^^^^

A copy of your measurement YAML, including the values returned by the pulse
sequence, plus:

- ``filename``: the common name of the ``.npz`` and ``.yaml`` file,
- ``measurement_status``: ``success`` or ``failed``,
- ``qupyt_version``,
- ``completed_averages``: the same numbers as ``averages`` in the ``.npz``,
  as a nested list ``[ps_step][dynamic_step]``.

.. _loading-data:

Loading data for analysis
^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from qupyt.measurement_logic.data_handling import load_measurement

   m = load_measurement("ODMR_cam_2026-04-21-14-03-12")   # .npz/.yaml optional

   m.data.shape        # (2, 1, 10, 1, 200, 300)
   m.axes              # ('channel', 'ps_step', 'dynamic_step', 'measurement', 'roi_0', 'roi_1')
   m.averages          # averages per (ps_step, dynamic_step)
   m.params["sensor"]  # everything from the .yaml file

   # Select by axis name instead of position:
   signal = m.sel(channel=0, ps_step=0, measurement=0)      # (10, 200, 300)
   reference = m.sel(channel=1, ps_step=0, measurement=0)
   contrast = signal.mean(axis=(1, 2)) / reference.mean(axis=(1, 2))

Without QuPyt installed, the file can be read with plain NumPy:

.. code-block:: python

   import numpy as np

   with np.load("ODMR_cam_2026-04-21-14-03-12.npz") as f:
       data = f["data"]
       axes = f["axes"].tolist()


For contributors
----------------

The data handling lives in :mod:`qupyt.measurement_logic.data_handling`:

- :class:`~qupyt.measurement_logic.data_handling.DataConfig` validates the
  ``data:`` block (pydantic model).
- :class:`~qupyt.measurement_logic.data_handling.MeasurementShape` computes
  the size of every axis from the parameters, the sensor and the config. It
  is the only place these sizes come from; the loops in ``run_measurement``
  use it too.
- :class:`~qupyt.measurement_logic.data_handling.MeasurementData` holds the
  running sum and the number of averages per step. Its ``add``, ``mean``,
  ``reset`` and ``save`` methods are the whole interface used by
  ``run_measurement``.

A new sensor only has to return an array of shape
``(number_measurements, *roi_shape)`` from ``acquire_data`` and set
``number_measurements`` and ``roi_shape``. The data container checks the
shape of every readout and raises a clear error if it does not match.

See :doc:`api_reference/data_handling` for the full API.

.. toctree::
   :maxdepth: 1

   upgrading
