"""Feature computation modules, one file per family.

Each module exposes a pure function that returns a dict of
``{stem: value}`` where ``stem`` matches the registry. The pipeline
applies the plausibility gate before emitting frame-level features.
"""
