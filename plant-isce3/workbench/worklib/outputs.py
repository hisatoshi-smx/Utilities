"""プロジェクト出力ファイル名の解決。"""

from __future__ import annotations

import os
import re

from .config import ExplorerConfig
from .projects import Project

_NAME_RE = re.compile(r"^[^\0/\\]+$")
_DEFAULTS = {"geocode": "geocode_geocode.tif", "rtc": "rtc_out.tif"}


def safe_basename(name: str, default: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return default
    norm = raw.replace("\\", "/")
    if "/" in norm or norm.startswith(".."):
        raise ValueError(f"出力ファイル名にパスを含められません: {name!r}")
    base = os.path.basename(norm)
    if not base or base in (".", "..") or not _NAME_RE.match(base):
        raise ValueError(f"出力ファイル名が不正です: {name!r}")
    low = base.lower()
    if not (low.endswith(".tif") or low.endswith(".tiff") or low.endswith(".vrt")):
        base += ".tif"
    return base


def output_path(proj: Project, tool: str, cfg: ExplorerConfig) -> str:
    default = _DEFAULTS.get(tool, f"{tool}_out.tif")
    custom = ""
    if cfg.outputs:
        custom = getattr(cfg.outputs, tool, "") or ""
    name = safe_basename(custom, default)
    return proj.path("output", name)