QuPyt Sensors
=============

QuPyt implements a host of sensor and their respective aqcuisition units.

Most sensor, and especially cameras provide a host of sensor configuration options.
For most sensors QuPyt implements setter methods for **many but not all** configurable values.
If you feel the need to add more options, feel free to contact the maintainer of the project or consider contributing the project directly.

Sensor
------

.. automodule:: qupyt.hardware.sensor.base
   :members:
   :show-inheritance:

Basler
------

.. automodule:: qupyt.hardware.sensor.basler
   :members:
   :show-inheritance:

DAQ
---

.. automodule:: qupyt.hardware.sensor.daq
   :members:
   :show-inheritance:

GenICam
-------

.. automodule:: qupyt.hardware.sensor.genicam
   :members:
   :show-inheritance:

HeliCam
-------

.. automodule:: qupyt.hardware.sensor.helicam
   :members:
   :show-inheritance:

Phantom
-------

.. automodule:: qupyt.hardware.sensor.phantom
   :members:
   :show-inheritance:

Mock Camera
-----------

.. automodule:: qupyt.hardware.sensor.mock
   :members:
   :show-inheritance:

Class Hierarchy
---------------

.. inheritance-diagram:: qupyt.hardware.sensor.base qupyt.hardware.sensor.basler qupyt.hardware.sensor.daq qupyt.hardware.sensor.genicam qupyt.hardware.sensor.helicam qupyt.hardware.sensor.phantom qupyt.hardware.sensor.mock
   :parts: 1
