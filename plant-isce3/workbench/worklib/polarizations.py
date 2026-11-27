"""偏波選択（HH / HV / 両方）の解釈。"""

from __future__ import annotations

import os

PolarizationMode = str  # HH | HV | HH_HV

VALID_MODES = frozenset({"HH", "HV", "HH_HV"})


def normalize_mode(value: str) -> str:
    v = (value or "HH").strip().upper()
    if v in ("HH_HV", "BOTH", "HH+HV", "HH,HV"):
        return "HH_HV"
    if v in VALID_MODES:
        return v
    raise ValueError(f"偏波は HH / HV / HH_HV のいずれか: {value!r}")


def polarizations_for(mode: str) -> list[str]:
    mode = normalize_mode(mode)
    if mode == "HH_HV":
        return ["HH", "HV"]
    return [mode]


def polarization_label(mode: str) -> str:
    mode = normalize_mode(mode)
    return mode


def split_output_per_pol(base_path: str, pol: str) -> str:
    root, ext = os.path.splitext(base_path)
    if ext.lower() not in (".tif", ".tiff", ".vrt"):
        ext = ".tif"
    return f"{root}_{pol}{ext}"