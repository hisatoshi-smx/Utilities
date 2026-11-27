"""
三時期 CCD の処理パイプライン。

ワーカープロセスとして起動する:
    python -m ccd3lib.pipeline --project NAME [--from-step STEP] [--only STEP]

各ステップは完了時に「設定の署名」を state.json に記録する。次回実行時、署名が
変わったステップ（とそれ以降）だけを再実行する。
"""

from __future__ import annotations

from . import envfix

envfix.apply()

import argparse  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime  # noqa: E402

from .config import PAIRS, SCENE_TAGS, STEP_LABELS, STEP_ORDER, check_runnable, step_signatures  # noqa: E402
from .projects import Project  # noqa: E402


COREG_MIN_NCC = 0.15


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Pipeline:
    def __init__(self, project: Project):
        self.p = project
        self.cfg = project.load()
        self.sigs = step_signatures(self.cfg)

    # ---------------------------------------------------------------- 共通
    def scenes(self) -> dict:
        data = self.p.read_json("work", "scenes.json")
        if not data:
            raise RuntimeError("シーン情報がありません（inspect ステップを先に実行してください）")
        return data

    def grid_info(self) -> dict:
        data = self.p.read_json("work", "grid.json")
        if not data:
            raise RuntimeError("グリッド情報がありません（grid ステップを先に実行してください）")
        return data

    def roi_bounds(self) -> list[float]:
        r = self.cfg.roi
        if r.is_set():
            return [r.west, r.south, r.east, r.north]
        return self.scenes()["common_bounds"]

    # ---------------------------------------------------------------- ステップ
    def step_inspect(self):
        from .inputs import inspect_scenes

        i = self.cfg.inputs
        res = inspect_scenes(i.scenes, i.sensor, i.polarization, i.nisar_frequency)
        for w in res["warnings"]:
            log(f"注意: {w}")
        for tag, s in res["scenes"].items():
            extra = f", バースト {len(s['bursts'])}" if s["bursts"] else ""
            log(f"{tag}: {s['datetime']}  {s['platform']}  track={s['track']}  {s['direction']}{extra}")
            log(f"     {s['path']}")
        self.p.write_json(res, "work", "scenes.json")

    def step_orbit(self):
        if self.cfg.inputs.sensor != "sentinel1":
            log("NISAR RSLC はプロダクト内の軌道を使用します")
            self.p.write_json({t: None for t in SCENE_TAGS}, "work", "orbits.json")
            return
        from .orbits import prepare_orbits

        o = self.cfg.orbit
        res = prepare_orbits(self.scenes()["scenes"], o.mode, o.files, self.cfg.inputs.scenes,
                             o.provider, o.netrc_file, self.p.path("work", "orbits"), log=log)
        self.p.write_json(res, "work", "orbits.json")

    def step_dem(self):
        from .dem import prepare_dem

        m = self.cfg.dem.margin_deg
        w, s, e, n = self.roi_bounds()
        bounds = [w - m, s - m, e + m, n + m]
        log(f"DEM 範囲（経緯度）: 西 {bounds[0]:.4f}, 南 {bounds[1]:.4f}, 東 {bounds[2]:.4f}, 北 {bounds[3]:.4f}")
        d = self.cfg.dem
        path = prepare_dem(d.source, d.path, d.vertical_datum, bounds, self.p.path("work", "dem"), log=log)
        self.p.write_json({"path": path, "bounds": bounds}, "work", "dem.json")

    def step_grid(self):
        from .grid import crs_name, grid_from_lonlat_bounds, subgrid_for_polygon, write_template
        from shapely.geometry import Polygon

        sc = self.scenes()
        bounds = self.roi_bounds()
        g = grid_from_lonlat_bounds(bounds, self.cfg.grid.epsg, self.cfg.grid.spacing)
        log(f"出力グリッド: EPSG:{g.epsg}（{crs_name(g.epsg)}）, {g.width} x {g.length} 画素, "
            f"間隔 {abs(g.dx):g} / {abs(g.dy):g}")

        tmpl_dir = self.p.path("work", "grid")
        if os.path.isdir(tmpl_dir):
            shutil.rmtree(tmpl_dir)
        units = []
        if self.cfg.inputs.sensor == "sentinel1":
            per_scene = [{b["id"]: b for b in sc["scenes"][t]["bursts"]} for t in SCENE_TAGS]
            common = sorted(set(per_scene[0]) & set(per_scene[1]) & set(per_scene[2]))
            wanted = self.cfg.inputs.burst_ids
            if wanted:
                missing = [b for b in wanted if b not in common]
                if missing:
                    raise RuntimeError(f"3 シーン共通でないバースト ID が指定されています: {missing}")
                common = [b for b in common if b in wanted]
            for bid in common:
                poly = None
                for ps in per_scene:
                    p = Polygon(ps[bid]["border"])
                    poly = p if poly is None else poly.intersection(p)
                if poly.is_empty:
                    continue
                sub = subgrid_for_polygon(g, list(poly.exterior.coords))
                if sub is None:
                    continue
                units.append({"id": bid, "grid": sub.to_dict(), "window": g.window(sub)})
            if not units:
                raise RuntimeError("ROI にかかる共通バーストがありません")
            log(f"処理するバースト（{len(units)} 個）: {', '.join(u['id'] for u in units)}")
        else:
            ring = sc["common_footprint"]
            sub = subgrid_for_polygon(g, ring)
            if sub is None:
                raise RuntimeError("ROI と 3 シーンの共通範囲が重なりません")
            units.append({"id": "frame", "grid": sub.to_dict(), "window": g.window(sub)})
            log(f"フレーム部分グリッド: {sub.width} x {sub.length} 画素")

        from .grid import Grid

        for u in units:
            u["template"] = write_template(os.path.join(tmpl_dir, f"{u['id']}.vrt"), Grid.from_dict(u["grid"]))
        self.p.write_json({"grid": g.to_dict(), "crs_name": crs_name(g.epsg), "lonlat_bounds": g.lonlat_bounds(),
                           "units": units}, "work", "grid.json")

    def step_geocode(self):
        from .geocode import geocode_nisar, geocode_s1_burst

        sc = self.scenes()["scenes"]
        gi = self.grid_info()
        orbits = self.p.read_json("work", "orbits.json", default={})
        dem = self.p.read_json("work", "dem.json")["path"]
        epsg = gi["grid"]["epsg"]
        i = self.cfg.inputs
        keep = self.cfg.processing.keep_intermediate
        interp = self.cfg.processing.data_interp_method
        tmpdir = self.p.path("work", "tmp")
        os.makedirs(tmpdir, exist_ok=True)
        cwd = os.getcwd()
        os.chdir(tmpdir)  # plant の一時ファイルをプロジェクト内に置く
        try:
            coreg = self.nisar_coreg(sc, gi, dem) if i.sensor != "sentinel1" else {}
            total = len(SCENE_TAGS) * len(gi["units"])
            k = 0
            for tag in SCENE_TAGS:
                for u in gi["units"]:
                    k += 1
                    out = self.p.path("work", "gslc", tag, f"{u['id']}.tif")
                    log(f"[{k}/{total}] {tag} {u['id']} → {os.path.relpath(out, self.p.root)}")
                    t0 = time.time()
                    slc_dir = self.p.path("work", "slc", tag)
                    if i.sensor == "sentinel1":
                        geocode_s1_burst(sc[tag]["path"], orbits.get(tag), u["id"], i.polarization, dem,
                                         u["template"], epsg, out, slc_dir, interp, keep)
                    else:
                        c = coreg.get(tag) or {}
                        geocode_nisar(sc[tag]["path"], i.nisar_frequency, i.polarization, dem,
                                      u["template"], epsg, out,
                                      c.get("az_time_s", 0.0), c.get("slant_range_m", 0.0))
                    log(f"    完了（{time.time() - t0:.0f} 秒）")
        finally:
            os.chdir(cwd)
            if not keep:
                shutil.rmtree(self.p.path("work", "slc"), ignore_errors=True)

    def nisar_coreg(self, sc: dict, gi: dict, dem: str) -> dict:
        """t2 を基準に t1 / t3 の残差オフセットを推定する（ROI 中心で）。"""
        from .geocode import estimate_nisar_offset

        g = gi["grid"]
        x = g["x0"] + g["dx"] * g["width"] / 2
        y = g["y0"] + g["dy"] * g["length"] / 2
        i = self.cfg.inputs
        res = {"t2": {"reference": True}}
        log("日付間の残差オフセット推定（基準 t2）")
        for tag in ("t1", "t3"):
            o = estimate_nisar_offset(sc["t2"]["path"], sc[tag]["path"], i.nisar_frequency,
                                      i.polarization, dem, g["epsg"], x, y)
            o["applied"] = o["ncc"] >= COREG_MIN_NCC
            log(f"  {tag}: 方位 {o['az_lines']:+.2f} 行, 距離 {o['rg_samples']:+.2f} サンプル"
                f"（相関 {o['ncc']:.2f}）")
            if not o["applied"]:
                o["az_time_s"] = o["slant_range_m"] = 0.0
                log(f"  注意: {tag} と t2 の振幅がほとんど相関しません（相関 {o['ncc']:.2f}）。"
                    "補正は適用しません。どちらかのシーンのフォーカス不良・"
                    "大規模な地表変化・別地点のデータなどが考えられ、CCD 結果は信頼できません。")
            res[tag] = o
        self.p.write_json(res, "work", "coreg.json")
        return res

    def step_coherence(self):
        from .coherence import coherence_file, mosaic
        from .grid import Grid

        gi = self.grid_info()
        g = Grid.from_dict(gi["grid"])
        c = self.cfg.ccd
        from osgeo import osr

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(g.epsg)
        wkt = srs.ExportToWkt()
        names = {"pre": "gamma_pre.tif", "co": "gamma_co.tif"}
        for pair, (ta, tb) in PAIRS.items():
            parts = []
            for u in gi["units"]:
                g1 = self.p.path("work", "gslc", ta, f"{u['id']}.tif")
                g2 = self.p.path("work", "gslc", tb, f"{u['id']}.tif")
                out = self.p.path("work", "coh", pair, f"{u['id']}.tif")
                log(f"{pair}（{ta}–{tb}） {u['id']}: 窓 {c.window_x} x {c.window_y}")
                coherence_file(g1, g2, out, c.window_y, c.window_x, c.min_valid_fraction)
                col, row = u["window"]
                parts.append((out, col, row))
            dst = self.p.path("output", names[pair])
            mosaic(parts, g.width, g.length, g.geotransform, wkt, dst, c.edge_erosion)
            log(f"  → {os.path.relpath(dst, self.p.root)}")

    def step_products(self):
        from .products import make_previews, make_products

        out = self.p.output
        summary = make_products(out, self.cfg.ccd.dgamma_range, log=log)
        previews = make_previews(out, self.cfg.ccd.dgamma_range)
        sc = self.scenes()["scenes"]
        summary["scenes"] = {t: {"datetime": s["datetime"], "path": s["path"]} for t, s in sc.items()}
        summary["grid"] = self.grid_info()["grid"]
        summary["created"] = _now()
        self.p.write_json(summary, "output", "summary.json")
        self.p.write_json(previews, "output", "preview", "preview.json")
        log("成果物:")
        for f in sorted(os.listdir(out)):
            fp = os.path.join(out, f)
            if os.path.isfile(fp):
                log(f"  {fp}  ({os.path.getsize(fp) / 1e6:.1f} MB)")

    # ---------------------------------------------------------------- 実行制御
    def plan(self, from_step: str | None, only: str | None) -> list[str]:
        if only:
            return [only]
        state = self.p.read_state()
        steps = list(STEP_ORDER)
        start = 0
        if from_step:
            start = steps.index(from_step)
        else:
            # 署名が一致する完了済みステップは飛ばす（最初の不一致以降を実行）
            for k, s in enumerate(steps):
                st = state["steps"].get(s, {})
                if st.get("status") == "done" and st.get("signature") == self.sigs[s]:
                    start = k + 1
                    continue
                break
        return steps[start:]

    def run(self, from_step: str | None = None, only: str | None = None) -> int:
        errors, warnings = check_runnable(self.cfg)
        for w in warnings:
            log(f"注意: {w}")
        if errors:
            for e in errors:
                log(f"エラー: {e}")
            return 2
        if self.cfg.processing.omp_threads:
            os.environ["OMP_NUM_THREADS"] = str(self.cfg.processing.omp_threads)

        steps = self.plan(from_step, only)
        if not steps:
            log("すべてのステップが完了済みで、設定にも変更はありません。")
            return 0
        # 再実行するステップ以降は未完了に戻す
        state = self.p.read_state()
        first = STEP_ORDER.index(steps[0])
        for s in STEP_ORDER[first:]:
            if not only or s == only:
                state["steps"][s] = {"status": "pending"}
        self.p.write_state(state)

        log(f"実行するステップ: {', '.join(steps)}")
        for s in steps:
            log("")
            log(f"===== {STEP_LABELS[s]}（{s}） =====")
            self.p.update_step(s, status="running", started=_now(), finished=None, message="")
            t0 = time.time()
            try:
                getattr(self, f"step_{s}")()
            except Exception as e:
                log(traceback.format_exc())
                log(f"エラー: {s} ステップで失敗しました: {e}")
                self.p.update_step(s, status="failed", finished=_now(), message=str(e)[:500])
                return 1
            el = time.time() - t0
            self.p.update_step(s, status="done", finished=_now(), elapsed=round(el, 1), signature=self.sigs[s],
                               message="")
            log(f"----- 完了（{el:.0f} 秒） -----")
        log("")
        log("処理が完了しました。")
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="plant-isce3 三時期 CCD パイプライン")
    ap.add_argument("--project", required=True)
    ap.add_argument("--projects-dir", default=None)
    ap.add_argument("--from-step", choices=STEP_ORDER)
    ap.add_argument("--only", choices=STEP_ORDER)
    args = ap.parse_args(argv)
    proj = Project(args.project, args.projects_dir)
    log(f"プロジェクト: {proj.name}  ({proj.root})")
    log(f"開始: {_now()}  Python: {sys.executable}")
    return Pipeline(proj).run(args.from_step, args.only)


if __name__ == "__main__":
    sys.exit(main())
