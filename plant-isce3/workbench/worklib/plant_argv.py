"""ワーカー subprocess 向けに plant の argv を整理する。"""

from __future__ import annotations

# plant の相互排他オプション（ログ・色）
_LOG_FLAGS = frozenset({"--log-enabled", "--no-log", "--log-disabled"})
_COLOR_FLAGS = frozenset({"--text-color", "--no-text-color", "--text_color"})


def prepare_plant_argv(argv: list[str]) -> list[str]:
    """ANSI 色を抑え、--log-enabled と --no-log の衝突を避ける。"""
    out: list[str] = []
    for a in argv:
        if a in _COLOR_FLAGS or a in _LOG_FLAGS:
            continue
        out.append(a)
    if "--no-text-color" not in out:
        out.append("--no-text-color")
    if "--no-log" not in out and "--log-enabled" not in out:
        out.append("--no-log")
    return out