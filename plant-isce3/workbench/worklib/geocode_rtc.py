"""plant_isce3_geocode 向け RTC 付きジオコードの argv プリセット。"""

from __future__ import annotations

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
        if a in _STRIP_FLAGS:
            i += 1
            continue
        out.append(a)
        i += 1
    if not _has_flag(out, _RTC_OUTPUT_FLAGS):
        out.insert(1, "--area-proj-gamma-naught")
    if "--terrain" not in out and "--rtc" not in out:
        out += ["--terrain", "gamma-naught-area-projection"]
    pol = (polarization or "").strip().upper()
    if pol and "--list-of-polarizations" not in out:
        out += ["--list-of-polarizations", pol]
    for flag in ("--force", "--no-log"):
        if flag not in out:
            out.append(flag)
    return out