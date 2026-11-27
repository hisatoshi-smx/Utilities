#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plant-isce3 三時期 CCD（コマンドライン版）

    python run_ccd3.py list
    python run_ccd3.py create NAME [--description TEXT]
    python run_ccd3.py check NAME
    python run_ccd3.py run NAME [--from-step STEP | --only STEP]
    python run_ccd3.py status NAME

設定は projects/NAME/project.yaml を直接編集するか、GUI で作成する。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ccd3lib import envfix  # noqa: E402

envfix.apply()

from ccd3lib.config import STEP_LABELS, STEP_ORDER, check_runnable, step_signatures  # noqa: E402
from ccd3lib.projects import Project, ProjectError, create_project, list_projects  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="plant-isce3 三時期 CCD（CLI）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="プロジェクト一覧")
    p = sub.add_parser("create", help="プロジェクト作成")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p = sub.add_parser("check", help="実行前チェック")
    p.add_argument("name")
    p = sub.add_parser("status", help="ステップの状態")
    p.add_argument("name")
    p = sub.add_parser("run", help="処理を実行（フォアグラウンド）")
    p.add_argument("name")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--from-step", choices=STEP_ORDER)
    g.add_argument("--only", choices=STEP_ORDER)
    args = ap.parse_args()

    try:
        if args.cmd == "list":
            for pr in list_projects():
                print(f"{pr['name']:24s} {pr['steps_done']}/{pr['steps_total']}  {pr['description']}")
            return 0
        if args.cmd == "create":
            proj = create_project(args.name, args.description)
            print(f"作成しました: {proj.config_path}")
            return 0
        proj = Project(args.name)
        cfg = proj.load()
        if args.cmd == "check":
            errors, warnings = check_runnable(cfg)
            for w in warnings:
                print(f"注意: {w}")
            for e in errors:
                print(f"エラー: {e}")
            print("OK" if not errors else "実行できません")
            return 1 if errors else 0
        if args.cmd == "status":
            state = proj.read_state()
            sigs = step_signatures(cfg)
            for s in STEP_ORDER:
                st = state["steps"][s]
                stale = " (設定変更あり)" if st.get("status") == "done" and st.get("signature") != sigs[s] else ""
                print(f"{s:10s} {st.get('status', 'pending'):10s} {STEP_LABELS[s]}{stale}")
            return 0
        if args.cmd == "run":
            from ccd3lib.pipeline import Pipeline

            return Pipeline(proj).run(args.from_step, args.only)
    except ProjectError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
