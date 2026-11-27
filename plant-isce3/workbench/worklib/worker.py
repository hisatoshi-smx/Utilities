"""GUI から起動するワーカー: python -m worklib.worker --project NAME --tool geocode"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from . import envfix

envfix.apply()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--projects-dir", default=None)
    ap.add_argument("--tool", required=True, choices=["geocode", "rtc"])
    args = ap.parse_args()
    from .pipeline import run_project_tool
    from .projects import Project

    proj = Project(args.project, args.projects_dir)
    try:
        rc = run_project_tool(proj, args.tool)
    except Exception:
        traceback.print_exc()
        return 1
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())