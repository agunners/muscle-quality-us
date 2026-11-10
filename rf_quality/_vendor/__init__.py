"""Vendored helpers copied verbatim from the upstream ``muscle_quality`` package.

See ``muscle_quality/muscle_quality/{roi,metrics}.py`` in the sibling repo —
we vendor only the 5–8 helpers the pipeline actually uses so the main repo
venv does not have to import the upstream package. Keep these byte-for-byte
aligned with upstream; an erosion-kernel equivalence test asserts agreement.
"""
