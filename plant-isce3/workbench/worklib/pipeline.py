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
from .band_tags import band_descriptions, tag_geotiff_bands
from .plant_argv import prepare_plant_argv
from .polarizations import polarizations_for, split_output_per_pol
from .projects import Project


def _log(msg: str) -> None:
    print(msg, flush=True)


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


def plant_exit_code(ret) -> int:
    """plant の main() は成功時に出力パス文字列を返すことがある。"""
    if ret is None:
        return 0
    if isinstance(ret, bool):
        return 0 if ret else 1
    if isinstance(ret, int):
        return ret
    if isinstance(ret, str):
        return 0 if ret else 1
    return 0


def run_tool(tool: str, argv: list[str], log=print) -> int:
    if tool not in TOOLS:
        raise KeyError(tool)
    mod = importlib.import_module(TOOLS[tool]["module"])
    log("plant " + " ".join(argv))
    try:
        return plant_exit_code(mod.main(argv))
    except SystemExit as e:
        return int(e.code or 0)


def run_project_tool(proj: Project, tool: str, log=_log) -> int:
    cfg = proj.load()
    meta = prepare_workspace(proj, cfg, log=log)
    if tool == "geocode":
        out = output_path(proj, tool, cfg)
        if cfg.geocode.output == "complex_gslc":
            from .nisar_geocode import geocode_nisar

            log("NISAR: isce3.geocode_slc（複素 GSLC・位相保持、RTC なし）")
            pols = polarizations_for(cfg.polarization)
            written: list[str] = []
            if len(pols) == 1:
                geocode_nisar(
                    cfg.input_h5,
                    cfg.frequency,
                    pols[0],
                    meta["dem_path"],
                    meta["template_vrt"],
                    meta["grid"]["epsg"],
                    out,
                )
                written.append(out)
            else:
                for pol in pols:
                    op = split_output_per_pol(out, pol)
                    geocode_nisar(
                        cfg.input_h5,
                        cfg.frequency,
                        pol,
                        meta["dem_path"],
                        meta["template_vrt"],
                        meta["grid"]["epsg"],
                        op,
                    )
                    written.append(op)
            labels = band_descriptions(cfg, "geocode")
            for i, p in enumerate(written):
                if not os.path.isfile(p):
                    raise RuntimeError(f"出力ファイルがありません: {p}")
                tag_geotiff_bands(p, [labels[i]] if i < len(labels) else labels[:1])
                log(f"出力: {p}（バンド名: {labels[i] if i < len(labels) else labels[0]}）")
            return 0
        argv = build_tool_argv(proj, cfg, tool)
        argv = apply_rtc_geocode_argv(argv, cfg.polarization)
        argv = prepare_plant_argv(argv)
        log("NISAR: plant_isce3_geocode（地図投影 + 放射地形補正 γ⁰）")
        rc = run_tool(tool, argv, log=log)
        if rc != 0:
            raise RuntimeError(f"plant geocode が失敗しました (exit code {rc})")
        if not os.path.isfile(out):
            raise RuntimeError(f"出力ファイルがありません: {out}")
        labels = band_descriptions(cfg, "geocode")
        tag_geotiff_bands(out, labels)
        log(f"出力: {out}（バンド: {', '.join(labels)}）")
        return rc
    argv = prepare_plant_argv(build_tool_argv(proj, cfg, tool))
    if tool == "rtc":
        from .geocode_rtc import apply_rtc_tool_argv

        argv = apply_rtc_tool_argv(argv, cfg.polarization)
    rc = run_tool(tool, argv, log=log)
    out = output_path(proj, tool, cfg)
    if rc != 0:
        raise RuntimeError(f"plant {tool} が失敗しました (exit code {rc})")
    if not os.path.isfile(out):
        raise RuntimeError(f"出力ファイルがありません: {out}")
    labels = band_descriptions(cfg, tool)
    tag_geotiff_bands(out, labels)
    log(f"出力: {out}（バンド: {', '.join(labels)}）")
    return rc