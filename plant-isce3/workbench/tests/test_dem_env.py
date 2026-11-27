import os
import subprocess

from worklib.dem_downloader_env import dem_downloader_cli, dem_downloader_python, subprocess_env_for


def test_dem_cli_path_exists():
    p = dem_downloader_cli()
    assert os.path.isfile(p)
    assert p.endswith("run_dem_cli.py")


def test_subprocess_env_uses_dem_downloader_proj():
    py = dem_downloader_python()
    prefix = os.path.dirname(os.path.dirname(py))
    env = subprocess_env_for(py)
    assert env["PROJ_DATA"].startswith(prefix)
    env["PROJ_DATA"] = "/wrong"
    proc = subprocess.run(
        [py, "-c", "from pyproj import CRS; print(CRS.from_epsg(4326))"],
        capture_output=True,
        text=True,
        env=subprocess_env_for(py),
    )
    assert proc.returncode == 0, proc.stderr