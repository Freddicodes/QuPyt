QuPyt Sensors
=============

QuPyt implements a host of sensor and their respective aqcuisition units.

Most sensor, and especially cameras provide a host of sensor configuration options.
For most sensors QuPyt implements setter methods for **many but not all** configurable values.
If you feel the need to add more options, feel free to contact the maintainer of the project or consider contributing the project directly.

Every sensor registers itself under one or more names. Use
``Sensor.create(name, configuration)`` to create one and
``Sensor.available()`` to list the registered names.

Base class
----------

.. automodule:: qupyt.hardware.sensor.base
   :members:
   :show-inheritance:

Mock
----

.. automodule:: qupyt.hardware.sensor.mock
   :members:
   :show-inheritance:

Basler
------

.. automodule:: qupyt.hardware.sensor.basler
   :members:
   :show-inheritance:

GenICam
-------

.. automodule:: qupyt.hardware.sensor.genicam
   :members:
   :show-inheritance:

Phantom
-------

.. automodule:: qupyt.hardware.sensor.phantom
   :members:
   :show-inheritance:

HeliCam
-------

.. automodule:: qupyt.hardware.sensor.helicam
   :members:
   :show-inheritance:

DAQ
---

.. automodule:: qupyt.hardware.sensor.daq
   :members:
   :show-inheritance:

Inheritance
-----------

.. inheritance-diagram:: qupyt.hardware.sensor.base qupyt.hardware.sensor.mock qupyt.hardware.sensor.basler qupyt.hardware.sensor.genicam qupyt.hardware.sensor.phantom qupyt.hardware.sensor.helicam qupyt.hardware.sensor.daq
