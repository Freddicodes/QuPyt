Pulse Sequences
===============

User pulse sequence scripts (``ps_path`` in the measurement YAML) build
``sequence_<n>.yaml`` files with :class:`~qupyt.pulse_sequences.yaml_sequence.YamlSequence`
and the macro classes in the same module. The synchronisers translate these
files with :mod:`qupyt.pulse_sequences.sequence_designer`.

Writing sequences
-----------------

.. automodule:: qupyt.pulse_sequences.yaml_sequence
   :members:

Running user scripts
--------------------

.. automodule:: qupyt.pulse_sequences.pulse_sequence_handler
   :members:

Translation for the synchronisers
---------------------------------

.. automodule:: qupyt.pulse_sequences.sequence_designer
   :members:
