"""The libvirt backend.

This package is deliberately **not** a ``Backend`` subclass yet. ``schema.py``,
``render.py`` and ``prepare.py`` are the offline half and import nothing
hypervisor-specific; ``connect``, ``preflight`` and ``destroy`` -- and with them
the class that binds all seven methods together, and the ``REGISTRY`` entry --
arrive next.

A class that satisfied the ABC with three methods raising ``NotImplementedError``
would instantiate cleanly and look finished. Leaving it abstract until it is whole
is the behaviour findings.md §3 asks the ABC for in the first place.
"""
