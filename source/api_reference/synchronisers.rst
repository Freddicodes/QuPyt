QuPyt Synchronisers
===================

Synchronisers play the pulse sequence that ties all other devices together
during a measurement step.

Every synchroniser registers itself under one or more names. Use
``Synchroniser.create(name, configuration, channel_mapping)`` to create one
and ``Synchroniser.available()`` to list the registered names. The name is
the ``type`` given in the ``synchroniser`` section of a measurement YAML file.

Base class
----------

.. automodule:: qupyt.hardware.synchroniser.base
   :members:
   :show-inheritance:

Tektronix AWG (``TekAWG``)
--------------------------

.. automodule:: qupyt.hardware.synchroniser.awgenerator
   :members:
   :show-inheritance:

Swabian PulseStreamer (``SwabInstPS``)
--------------------------------------

.. automodule:: qupyt.hardware.synchroniser.pstreamer
   :members:
   :show-inheritance:

SpinCore PulseBlaster (``PulseBlaster``)
----------------------------------------

.. automodule:: qupyt.hardware.synchroniser.pulseblaster
   :members:
   :show-inheritance:

Mock (``MockSynchroniser``)
---------------------------

.. automodule:: qupyt.hardware.synchroniser.mock
   :members:
   :show-inheritance:

Inheritance
-----------

.. inheritance-diagram:: qupyt.hardware.synchroniser.base qupyt.hardware.synchroniser.awgenerator qupyt.hardware.synchroniser.pstreamer qupyt.hardware.synchroniser.pulseblaster qupyt.hardware.synchroniser.mock
