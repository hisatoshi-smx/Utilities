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
    direction = (info.get("direction") or "").strip().upper()
    if direction and direction[0] in "AN":  # Ascending / Northbound 等の先頭
        parts.append(direction[0])
    stem = "_".join(parts)
    geo_kind = "gslc" if geocode_mode == "complex_gslc" else "rtc_gamma"
    return {
        "geocode": f"{stem}_geocode_{geo_kind}.tif",
        "rtc": f"{stem}_rtc_radar.tif",
    }