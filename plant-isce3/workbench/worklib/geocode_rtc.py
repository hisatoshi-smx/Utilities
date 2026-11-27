"""plant_isce3_geocode 向け RTC 付きジオコードの argv プリセット。"""

from __future__ import annotations

from .polarizations import polarizations_for

_RTC_OUTPUT_FLAGS = frozenset(
    {
        "--area-proj-gamma-naught",
        "--area-gamma-naught",
        "--area-projection-gamma-naught",
        "--interp-gamma-naught",
        "--point-gamma-naught",
    }
)

_STRIP_FLAGS = frozenset(
    {
        "--gslc",
        "--interp",
        "--point",
        "--area",
        "--area-proj",
        "--area-projection",
    }
)


def _has_flag(argv: list[str], names: frozenset[str]) -> bool:
    return any(a in names for a in argv)


def apply_rtc_geocode_argv(argv: list[str], polarization: str) -> list[str]:
    """複素 GSLC 用オプションを除き、area-projection + γ⁰ RTC を付与する。"""
    if not argv:
        return argv
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--terrain" and i + 1 < len(argv) and argv[i + 1].lower() == "none":
            i += 2
            continue
        if a == "--list-of-polarizations":
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        if a in _STRIP_FLAGS:
            i += 1
            continue
        out.append(a)
        i += 1
    if not _has_flag(out, _RTC_OUTPUT_FLAGS):
        out.insert(1, "--area-proj-gamma-naught")
    if "--terrain" not in out and "--rtc" not in out:
        out += ["--terrain", "gamma-naught-area-projection"]
    pols = polarizations_for(polarization)
    if pols:
        out += ["--list-of-polarizations", *pols]
    if "--force" not in out:
        out.append("--force")
    return out


def apply_rtc_tool_argv(argv: list[str], polarization: str) -> list[str]:
    """rtc タブ用: geocode 専用フラグを除き、偏波はバンド名付与で反映（plant rtc は全偏波処理）。"""
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _RTC_OUTPUT_FLAGS or a in _STRIP_FLAGS:
            i += 1
            continue
        if a == "--area-proj-gamma-naught" or a == "--list-of-polarizations":
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        out.append(a)
        i += 1
    if "--force" not in out:
        out.append("--force")
    _ = polarization  # バンド Description のみ共通タブの選択に従う
    return out