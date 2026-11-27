"""フォーム値から plant CLI の argv リストを組み立てる。"""

from __future__ import annotations

from typing import Any


def build_argv(
    *,
    input_h5: str,
    dem_path: str,
    bbox_file: str,
    epsg: int,
    output_path: str,
    frequency: str,
    tool_args: dict[str, Any],
    schema_fields: list[dict],
) -> list[str]:
    """位置引数とオプションをまとめる。"""
    argv = [input_h5]
    argv += ["--dem", dem_path, "--bbox-file", bbox_file, "--epsg", str(epsg), "--output", output_path,
             "--frequency", frequency]
    for field in schema_fields:
        dest = field["dest"]
        val = tool_args.get(dest)
        if val is None or val == "":
            continue
        flags = field["flags"]
        if not flags:
            continue
        opt = flags[-1] if len(flags[-1]) > 2 else flags[0]
        t = field["type"]
        if t == "bool_true":
            if val:
                argv.append(opt)
        elif t == "bool_false":
            if not val:
                argv.append(opt)
        elif t == "list":
            argv.append(opt)
            argv += [str(x) for x in val]
        else:
            argv += [opt, str(val)]
    return argv