#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plant-isce3 三時期 CCD（ブラウザ GUI）

    conda activate plant-isce3
    python run_ccd3_gui.py                  # http://127.0.0.1:8790 で起動し、ブラウザを開く
    python run_ccd3_gui.py --port 9000 --no-browser

既定のポートが使用中なら、次の番号（最大 30 個先まで）を順に試す。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ccd3lib import envfix  # noqa: E402

envfix.apply()

from ccd3lib import jobs  # noqa: E402
from ccd3lib.projects import PROJECTS_DIR  # noqa: E402
from ccd3lib.server import make_server  # noqa: E402

# 他のブラウザ GUI（dem_downloader は 8780）と重ならないようにする
DEFAULT_PORT = 8790
PORT_TRIES = 30


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="plant-isce3 三時期 CCD GUI")
    ap.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス（既定: 127.0.0.1）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args()

    os.makedirs(PROJECTS_DIR, exist_ok=True)
    jobs.recover_stale()

    port = None
    for p in range(args.port, args.port + PORT_TRIES):
        if _port_free(args.host, p):
            port = p
            break
    if port is None:
        print(f"空きポートが見つかりません（{args.port}〜{args.port + PORT_TRIES - 1}）", file=sys.stderr)
        return 1

    srv = make_server(args.host, port)
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown}:{port}/"
    print(f"plant-isce3 三時期 CCD GUI: {url}")
    print(f"プロジェクト保存先: {PROJECTS_DIR}")
    print("終了するには Ctrl+C（または画面右上の「サーバー終了」）")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("警告: ループバック以外で待ち受けています。ファイルブラウザで他人にファイル一覧が見える可能性があります。")
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
