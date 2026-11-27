import pytest

from ccd3lib.grid import Grid, grid_from_lonlat_bounds, subgrid_for_polygon, utm_epsg


@pytest.mark.parametrize(
    "lon,lat,epsg",
    [(139.7, 35.7, 32654), (136.9, 37.4, 32653), (-115.6, 34.8, 32611), (151.2, -33.9, 32756)],
)
def test_utm(lon, lat, epsg):
    assert utm_epsg(lon, lat) == epsg


def test_grid_snapped_and_covers_roi():
    roi = [136.80, 37.30, 137.00, 37.50]
    g = grid_from_lonlat_bounds(roi, None, 10.0)
    assert g.epsg == 32653
    assert g.x0 % 10 == 0 and g.y0 % 10 == 0
    w, s, e, n = g.lonlat_bounds()
    assert w <= roi[0] and s <= roi[1] and e >= roi[2] and n >= roi[3]


def test_subgrid_aligned():
    g = grid_from_lonlat_bounds([136.80, 37.30, 137.00, 37.50], None, 10.0)
    ring = [[136.85, 37.35], [136.95, 37.35], [136.95, 37.45], [136.85, 37.45], [136.85, 37.35]]
    sub = subgrid_for_polygon(g, ring)
    col, row = g.window(sub)
    assert sub.x0 == pytest.approx(g.x0 + col * g.dx)
    assert sub.y0 == pytest.approx(g.y0 + row * g.dy)
    assert 0 <= col and col + sub.width <= g.width
    assert 0 <= row and row + sub.length <= g.length
    # ROI 外のポリゴンは None
    assert subgrid_for_polygon(g, [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]) is None


def test_roundtrip_dict():
    g = Grid(32653, 100.0, 200.0, 10.0, -10.0, 5, 6)
    assert Grid.from_dict(g.to_dict()) == g
