#!/usr/bin/env python3
"""Capture DEM GUI screenshot for README (Playwright)."""
from __future__ import annotations

import os
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(APP_DIR, "docs", "screenshot.png")
PORT = 8799


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, os.path.join(APP_DIR, "run_dem_gui.py"), "--port", str(PORT), "--no-browser"],
        cwd=APP_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            if proc.poll() is not None:
                print(proc.stdout.read() if proc.stdout else "")
                raise RuntimeError("run_dem_gui exited early")
            try:
                import urllib.request

                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("server did not start")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.wait_for_timeout(2500)
            page.screenshot(path=OUT, full_page=True)
            browser.close()
        print("wrote", OUT)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
