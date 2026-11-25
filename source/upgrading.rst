Upgrading from Older Versions
=============================

This version changes the ``data:`` block of the measurement YAML and the
format of the result files. Old configs are not converted automatically:
an old key in the ``data:`` block stops the measurement with
``Extra inputs are not permitted``.

Measurement YAML
----------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Old
     - New
   * - ``data.dynamic_steps``
     - Remove it. The top-level ``dynamic_steps`` is used.
   * - ``data.ps_steps``
     - Remove it. Set the top-level ``pulse_sequence_steps`` instead.
   * - ``data.number_measurements``, ``data.roi_shape``
     - Remove them. Both are taken from the sensor.
   * - ``data.compress``
     - Rename to ``data.live_compression``.

Before:

.. code-block:: yaml

   pulse_sequence_steps: &ps_steps 10
   dynamic_steps: &n_dynamic_steps 5

   data:
     averaging_mode: 'sum'
     dynamic_steps: *n_dynamic_steps
     ps_steps: *ps_steps
     compress: false
     reference_channels: 2

After:

.. code-block:: yaml

   pulse_sequence_steps: 10
   dynamic_steps: 5

   data:
     averaging_mode: 'sum'
     live_compression: false
     reference_channels: 2

Result files
------------

- Results are ``.npz`` files instead of ``.npy``. Replace
  ``np.load("x.npy")`` with ``np.load("x.npz")["data"]``, or use
  ``load_measurement`` (see :ref:`loading-data`).
- The data is always ``float64``, no longer the sensor's data type.
- Chunk files are ``.npz`` as well and have ``NaN`` (not ``0``) in the steps
  that were not measured in that chunk.

Failed measurements
-------------------

- Failed measurements are now saved as the mean over the averages that were
  recorded, not as a raw sum. Steps without any average are ``NaN``.
- The ``.yaml`` file has a new ``completed_averages`` entry with the number of
  averages per pulse-sequence and dynamic step.
- In ``sum`` mode, ``number_measurements`` must now be divisible by
  ``reference_channels`` (before, this was only checked in ``spread`` mode).
