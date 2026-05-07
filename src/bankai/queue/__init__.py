"""Job queue domain.

Sub-modules:

* ``models`` â€” pure data classes (no DB / no asyncio).
* ``worker`` â€” :class:`Dispatcher`, :class:`Worker`, error types.

We deliberately don't re-export ``worker`` here to avoid a circular import
with :mod:`bankai.db.state` (which only needs ``models``).
"""
