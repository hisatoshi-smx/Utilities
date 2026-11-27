"""DEM・グリッド準備と plant CLI 実行。"""

from __future__ import annotations

import importlib
import os

from .argv import build_argv
from .catalog import TOOLS, load_tool_parser, tool_schema
from .config import ExplorerConfig
from .dem import prepare_dem
from .geocode_rtc import apply_rtc_geocode_argv
from .grid import crs_name, grid_from_roi, roi_bounds, write_template
from .nisar_grid import resolve_spacing_m
from .outputs import output_path
from .projects import Project


def prepare_workspace(proj: Project, cfg: ExplorerConfig, log=print) -> dict:
    if not cfg.roi.is_set():
        raise RuntimeError("ROI が未設定です")
    bounds = roi_bounds(cfg.roi, cfg.dem.margin_deg)
    spacing_m, spacing_note = resolve_spacing_m(cfg)
    log(spacing_note)
    grid = grid_from_roi(cfg.roi, cfg.grid.epsg, spacing_m)
    tmpl = write_template(proj.path("work", "grid", "template.vrt"), grid)
    dem_path = prepare_dem(
        cfg.dem.source,
        cfg.dem.path,
        cfg.dem.gsi_mode,
        cfg.dem.vertical_datum,
        bounds,
        proj.path("work", "dem"),
        log=log,
    )
    meta = {
        "grid": grid.to_dict(),
        "grid_spacing_m": spacing_m,
        "grid_spacing_note": spacing_note,
        "grid_crs_name": crs_name(grid.epsg),
        "dem_path": dem_path,
        "template_vrt": tmpl,
    }
    proj.write_json(meta, "work", "runtime.json")
    return meta


def build_tool_argv(proj: Project, cfg: ExplorerConfig, tool: str) -> list[str]:
    rt = proj.read_json("work", "runtime.json")
    if not rt:
        raise RuntimeError("work/runtime.json がありません（準備が未実行）")
    schema = tool_schema(tool)
    out = output_path(proj, tool, cfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return build_argv(
        input_h5=cfg.input_h5,
        dem_path=rt["dem_path"],
        bbox_file=rt["template_vrt"],
        epsg=rt["grid"]["epsg"],
        output_path=out,
        frequency=cfg.frequency,
        tool_args=cfg.tools.get(tool) or {},
        schema_fields=schema["fields"],
    )


def run_tool(tool: str, argv: list[str], log=print) -> int:
    if tool not in TOOLS:
        raise KeyError(tool)
    mod = importlib.import_module(TOOLS[tool]["module"])
    log("plant " + " ".join(argv))
    try:
        return mod.main(argv) or 0
    except SystemExit as e:
        return int(e.code or 0)


def run_project_tool(proj: Project, tool: str, log=print) -> int:
    cfg = proj.load()
    meta = prepare_workspace(proj, cfg, log=log)
    if tool == "geocode":
        out = output_path(proj, tool, cfg)
        if cfg.geocode.output == "complex_gslc":
            from .nisar_geocode import geocode_nisar

            log("NISAR: isce3.geocode_slc（複素 GSLC・位相保持、RTC なし）")
            geocode_nisar(
                cfg.input_h5,
                cfg.frequency,
                cfg.polarization,
                meta["dem_path"],
                meta["template_vrt"],
                meta["grid"]["epsg"],
                out,
            )
            log(f"出力: {out}")
            return 0
        argv = build_tool_argv(proj, cfg, tool)
        argv = apply_rtc_geocode_argv(argv, cfg.polarization)
        log("NISAR: plant_isce3_geocode（地図投影 + 放射地形補正 γ⁰）")
        rc = run_tool(tool, argv, log=log)
        log(f"出力: {out}")
        return rc
    argv = build_tool_argv(proj, cfg, tool)
    return run_tool(tool, argv, log=log)