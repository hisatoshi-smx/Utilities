"""NISAR メタデータから一意な出力ファイル名を組み立てる。"""

from __future__ import annotations

import os
import re
from typing import Any

from .polarizations import polarization_label


def _date_yyyymmdd(datetime_str: str, h5_path: str) -> str:
    if datetime_str:
        digits = re.sub(r"[^0-9]", "", datetime_str.replace("T", "")[:15])
        if len(digits) >= 8:
            return digits[:8]
    base = os.path.basename(h5_path or "")
    m = re.search(r"(\d{8})T\d{6}", base)
    if m:
        return m.group(1)
    return "nodate"


def _sat_slug(info: dict[str, Any]) -> str:
    plat = (info.get("platform") or "NISAR").strip()
    return re.sub(r"[^A-Za-z0-9]+", "", plat) or "NISAR"


def _direction_code(direction: str) -> str | None:
    """軌道方向: Ascending→A, Descending→D（NISAR orbitPassDirection）。"""
    d = (direction or "").strip().upper()
    if not d:
        return None
    if d.startswith("ASC"):
        return "A"
    if d.startswith("DESC"):
        return "D"
    if d[0] in "AD":
        return d[0]
    return None


def suggest_output_basenames(
    info: dict[str, Any],
    *,
    frequency: str,
    polarization: str,
    geocode_mode: str,
) -> dict[str, str]:
    """geocode / rtc 用の推奨ベース名（拡張子付き）。"""
    sat = _sat_slug(info)
    parts = [sat]
    track = info.get("track")
    if track is not None:
        parts.append(f"tr{int(track)}")
    frame = info.get("frame")
    if frame is not None:
        parts.append(f"fr{int(frame)}")
    freq = (frequency or "A").strip().upper()
    parts.append(f"f{freq}")
    parts.append(polarization_label(polarization))
    parts.append(_date_yyyymmdd(info.get("datetime") or "", info.get("path") or ""))
    dir_code = _direction_code(info.get("direction") or "")
    if dir_code:
        parts.append(dir_code)
    stem = "_".join(parts)
    geo_kind = "gslc" if geocode_mode == "complex_gslc" else "rtc_gamma"
    return {
        "geocode": f"{stem}_geocode_{geo_kind}.tif",
        "rtc": f"{stem}_rtc_radar.tif",
    }