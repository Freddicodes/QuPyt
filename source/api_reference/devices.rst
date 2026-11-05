QuPyt Devices
=============

Devices are all parts of the setup that are neither the sensor nor the
synchroniser, e.g. signal sources or power supplies. They are listed under
``static_devices`` or ``dynamic_devices`` in the measurement YAML file and
managed by the :doc:`device_handler`.

Every device registers itself under one or more names. Use
``Device.create(device_type, address, configuration)`` to create one and
``Device.available()`` to list the registered names. The name is the
``device_type`` given in the YAML file::

    static_devices:
      mw_source:
        device_type: 'SMB'
        address: 'TCPIP::<ip>::INSTR'
        config:
          frequency: ['channel_1', 2.87e9]

An unknown ``device_type`` raises ``UnknownDeviceError``, which lists the
available names.

Base class
----------

.. automodule:: qupyt.hardware.device.base
   :members:
   :show-inheritance:

Signal sources
--------------

QuPyt implements a range of signal sources. Wherever practically possible,
amplitudes are interpreted in units of dBm. All of them share the
:class:`~qupyt.hardware.device.signal_sources.SignalSource` base class.

.. automodule:: qupyt.hardware.device.signal_sources
   :members:
   :show-inheritance:

Mock (``Mock``)
^^^^^^^^^^^^^^^

.. automodule:: qupyt.hardware.device.signal_mock
   :members:
   :show-inheritance:

VISA (``SRS``, ``TekAWG``, ``SMA``, ``SMB``, ``Rigol``, ``TekAFG``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: qupyt.hardware.device.signal_visa
   :members:
   :show-inheritance:

WindFreak (``WindFreak``, ``WindFreakHDM``, ``WindFreakSNV``, ``WindFreakSHDMini``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. automodule:: qupyt.hardware.device.signal_windfreak
   :members:
   :show-inheritance:

Power supplies
--------------

.. automodule:: qupyt.hardware.device.power_supply
   :members:
   :show-inheritance:

Inheritance
-----------

.. inheritance-diagram:: qupyt.hardware.device.base qupyt.hardware.device.signal_sources qupyt.hardware.device.signal_mock qupyt.hardware.device.signal_visa qupyt.hardware.device.signal_windfreak qupyt.hardware.device.power_supply
