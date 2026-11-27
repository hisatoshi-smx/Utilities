"""dem_downloader 専用 conda 環境の Python を解決する。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .logutil import log_cmd_multiline, run_process_logging


def dem_downloader_python() -> str:
    for key in ("DEM_DOWNLOADER_PYTHON", "EXPLORER_DEM_DOWNLOADER_PYTHON"):
        p = (os.environ.get(key) or "").strip()
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    prefix = os.environ.get("CONDA_PREFIX", "")
    if prefix:
        sib = os.path.join(os.path.dirname(prefix), "dem_downloader", "bin", "python")
        if os.path.isfile(sib):
            return os.path.abspath(sib)
    for name in ("mamba", "conda", "micromamba"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run(
                [exe, "run", "-n", "dem_downloader", "python", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if out.returncode == 0 and out.stdout.strip():
                p = out.stdout.strip().splitlines()[-1]
                if os.path.isfile(p):
                    return p
        except (OSError, subprocess.TimeoutExpired):
            continue
    for c in (
        os.path.expanduser("~/miniforge3/envs/dem_downloader/bin/python"),
        os.path.expanduser("~/mambaforge/envs/dem_downloader/bin/python"),
        os.path.expanduser("~/anaconda3/envs/dem_downloader/bin/python"),
    ):
        if os.path.isfile(c):
            return os.path.abspath(c)
    raise RuntimeError(
        "conda 環境「dem_downloader」が見つかりません。\n"
        "  cd dem_downloader && mamba env create -f environment.yml\n"
        "または環境変数 DEM_DOWNLOADER_PYTHON にその env の python パスを設定してください。"
    )


def _env_prefix(py: str) -> str:
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(py)))
    if os.path.isdir(os.path.join(prefix, "conda-meta")):
        return prefix
    return sys.prefix


def subprocess_env_for(py: str) -> dict:
    """親（plant-isce3）の GDAL/PROJ 変数を渡さず、指定 Python の conda 環境用に組み立てる。"""
    env = os.environ.copy()
    for key in (
        "GDAL_DRIVER_PATH",
        "GDAL_DATA",
        "PROJ_DATA",
        "PROJ_LIB",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
    ):
        env.pop(key, None)
    prefix = _env_prefix(py)
    for var, sub in (("GDAL_DATA", ("share", "gdal")), ("PROJ_DATA", ("share", "proj"))):
        p = os.path.join(prefix, *sub)
        if os.path.isdir(p):
            env[var] = p
    gdal_drv = os.path.join(prefix, "lib", "gdalplugins")
    if os.path.isdir(gdal_drv):
        env["GDAL_DRIVER_PATH"] = gdal_drv
    env.setdefault("PROJ_NETWORK", "ON")
    env["CONDA_PREFIX"] = prefix
    return env


def dem_downloader_cli() -> str:
    # plant-isce3/workbench から smx/dem_downloader を探す
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(os.path.join(here, "..", "..", "..", "dem_downloader", "run_dem_cli.py")),
        os.path.abspath(os.path.join(here, "..", "..", "dem_downloader", "run_dem_cli.py")),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    raise RuntimeError("dem_downloader/run_dem_cli.py が見つかりません（リポジトリの dem_downloader を配置してください）")


def run_gsi_download(
    bounds: list[float],
    mode: str,
    out_path: str,
    log=print,
) -> str:
    """別環境 dem_downloader で国土地理院 DEM を取得する。"""
    py = dem_downloader_python()
    cli = dem_downloader_cli()
    w, s, e, n = bounds
    cmd = [
        py,
        cli,
        "--west",
        str(w),
        "--south",
        str(s),
        "--east",
        str(e),
        "--north",
        str(n),
        "--output",
        os.path.abspath(out_path),
        "--mode",
        mode,
        "--height-mode",
        "ellipsoid",
        "--crs",
        "EPSG:4326",
    ]
    log(f"  dem_downloader: {py}")
    log_cmd_multiline(log, cmd)
    env = subprocess_env_for(py)
    rc = run_process_logging(cmd, log, cwd=os.path.dirname(cli), env=env)
    if rc != 0:
        raise RuntimeError(f"国土地理院 DEM の取得に失敗しました (code {rc})")
    if not os.path.isfile(out_path):
        raise RuntimeError(f"出力がありません: {out_path}")
    return out_path