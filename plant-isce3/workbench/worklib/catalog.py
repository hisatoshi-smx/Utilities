"""
plant-isce3 各ツールの argparse を JSON スキーマ化（GUI 自動生成用）。
"""

from __future__ import annotations

import argparse
import importlib
from typing import Any

# 第1版で GUI に載せるツール
TOOLS: dict[str, dict[str, str]] = {
    "geocode": {"module": "plant_isce3.plant_isce3_geocode", "label": "ジオコード (geocode)"},
    "rtc": {"module": "plant_isce3.plant_isce3_rtc", "label": "地形補正 (rtc)"},
}

# プロジェクト側で自動投入する dest（フォームから隠す）
PINNED: dict[str, tuple[str, ...]] = {
    "geocode": (
        "input_file",
        "dem_file",
        "bbox_file",
        "epsg",
        "output_file",
        "frequency",
    ),
    "rtc": (
        "input_file",
        "dem_file",
        "bbox_file",
        "epsg",
        "output_file",
        "frequency",
    ),
}


def _action_type(action: argparse.Action) -> str:
    if isinstance(action, argparse._StoreTrueAction):
        return "bool_true"
    if isinstance(action, argparse._StoreFalseAction):
        return "bool_false"
    if action.choices:
        return "choice"
    t = action.type
    if t is int:
        return "int"
    if t is float:
        return "float"
    if getattr(action, "nargs", None) in ("+", "*"):
        return "list"
    return "str"


def _action_default(action: argparse.Action) -> Any:
    if action.default is argparse.SUPPRESS:
        return None
    if action.default is None:
        return None
    if isinstance(action.default, (str, int, float, bool)):
        return action.default
    return str(action.default)


def parser_schema(parser: argparse.ArgumentParser, tool: str) -> dict:
    pinned = set(PINNED.get(tool, ()))
    fields = []
    for g in parser._action_groups:
        title = (g.title or "").strip() or "options"
        for a in g._group_actions:
            if a.dest in ("help", "version") or a.dest in pinned:
                continue
            if not a.option_strings and a.dest == "input_file":
                continue
            if not a.option_strings:
                continue
            fields.append({
                "dest": a.dest,
                "flags": a.option_strings,
                "type": _action_type(a),
                "required": bool(getattr(a, "required", False)),
                "default": _action_default(a),
                "choices": list(a.choices) if a.choices else None,
                "help": (a.help or "").strip(),
                "group": title,
            })
    return {"tool": tool, "label": TOOLS[tool]["label"], "fields": fields, "pinned": list(PINNED.get(tool, ()))}


def load_tool_parser(tool: str) -> argparse.ArgumentParser:
    if tool not in TOOLS:
        raise KeyError(f"不明なツール: {tool}")
    mod = importlib.import_module(TOOLS[tool]["module"])
    return mod.get_parser()


def tool_schema(tool: str) -> dict:
    return parser_schema(load_tool_parser(tool), tool)


def all_tools() -> list[dict]:
    return [{"id": k, "label": v["label"], "module": v["module"]} for k, v in TOOLS.items()]


def main() -> None:
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        out = {t: tool_schema(t) for t in TOOLS}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for t in TOOLS:
            s = tool_schema(t)
            print(f"{t}: {len(s['fields'])} 項目")


if __name__ == "__main__":
    main()