from __future__ import annotations

import warnings


def _format_zd_time(ident) -> str:
    zd = getattr(ident, "zdStartTime", None)
    if zd is None:
        return ""
    dt = str(zd).replace("T", " ")
    if "." in dt:
        head, frac = dt.split(".", 1)
        dt = f"{head}.{frac[:6]}"
    return dt[:19] if len(dt) > 19 else dt


def _track_number(ident, h5_path: str) -> int | None:
    if getattr(ident, "trackNumber", None) is not None:
        try:
            return int(ident.trackNumber)
        except (TypeError, ValueError):
            pass
    try:
        import h5py

        with h5py.File(h5_path, "r") as h5:
            for root in ("science/LSAR", "science/SSAR"):
                key = f"{root}/identification/trackNumber"
                if key in h5:
                    return int(h5[key][()])
    except OSError:
        pass
    return None


def _bytes_str(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v) if v is not None else ""


def inspect_nisar(h5_path: str, frequency: str = "A") -> dict:
    import shapely.wkt
    import shapely
    from shapely.geometry import MultiPolygon
    from plant_isce3.readers import open_product

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prod = open_product(h5_path)
    if getattr(prod, "productType", None) not in (None, "RSLC", "SLC", "GSLC"):
        raise ValueError(f"RSLC 系ではない可能性があります（productType={prod.productType}）")

    ident = prod.identification
    poly = shapely.force_2d(shapely.wkt.loads(ident.boundingPolygon))
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    ring = [[round(c[0], 6), round(c[1], 6)] for c in poly.exterior.coords]
    bounds = list(poly.bounds)
    freqs = []
    if ident.listOfFrequencies is not None:
        freqs = [_bytes_str(f) for f in ident.listOfFrequencies]
    pols_available: list[str] = []
    try:
        import h5py

        freq_key = frequency if frequency in (freqs or [frequency]) else (freqs[0] if freqs else frequency)
        with h5py.File(h5_path, "r") as h5:
            for root in ("science/LSAR", "science/SSAR"):
                key = f"{root}/RSLC/swaths/frequency{freq_key}/listOfPolarizations"
                if key in h5:
                    pols_available = [
                        _bytes_str(p) for p in h5[key][()]
                    ]
                    break
    except OSError:
        pass
    track = _track_number(ident, h5_path)
    frame = getattr(ident, "frameNumber", None)
    try:
        frame = int(frame) if frame is not None else None
    except (TypeError, ValueError):
        frame = None
    spacing = {}
    try:
        from .nisar_grid import nominal_ground_spacing_m

        freq = frequency if frequency in (freqs or [frequency]) else (freqs[0] if freqs else frequency)
        az, rg, rec = nominal_ground_spacing_m(h5_path, freq)
        spacing = {
            "azimuth_m": round(az, 3),
            "ground_range_m": round(rg, 3),
            "recommended_m": round(max(0.25, round(rec, 3)), 3),
            "frequency": freq,
        }
    except Exception:
        pass
    return {
        "path": h5_path,
        "platform": _bytes_str(getattr(ident, "missionId", None)) or "NISAR",
        "datetime": _format_zd_time(ident),
        "track": track,
        "frame": frame,
        "direction": _bytes_str(ident.orbitPassDirection),
        "frequencies": freqs,
        "polarizations_available": pols_available,
        "footprint": ring,
        "bounds": [bounds[0], bounds[1], bounds[2], bounds[3]],
        "spacing": spacing,
    }