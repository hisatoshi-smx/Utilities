#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Plant-ISCE3 エクスプローラ（ブラウザ GUI）"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worklib import envfix

envfix.apply()

from worklib import jobs
from worklib.projects import PROJECTS_DIR
from worklib.server import make_server

DEFAULT_PORT = 8785
PORT_TRIES = 30


def _free(host: str, port: int) -> bool:
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Plant-ISCE3 エクスプローラ")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    jobs.recover_stale()
    port = next((p for p in range(args.port, args.port + PORT_TRIES) if _free(args.host, p)), None)
    if port is None:
        print("空きポートがありません", file=sys.stderr)
        return 1
    srv = make_server(args.host, port)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown}:{port}/"
    print(f"Plant-ISCE3 エクスプローラ: {url}")
    print(f"プロジェクト: {PROJECTS_DIR}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    print("サーバーを停止しました（実行中の処理はバックグラウンドで継続します）")
    return 0


if __name__ == "__main__":
    sys.exit(main())