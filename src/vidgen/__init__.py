"""The video engine behind AI Video Studio.

Pure modules - ``script``, ``timeline``, ``formats`` - import only the standard
library, so the window can use them at startup and the tests can run without
MoviePy or a display. ``motion`` and ``audio`` pull in MoviePy and are imported
lazily by ``render``, which keeps the window appearing instantly.
"""
