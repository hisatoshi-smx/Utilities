from worklib.config import ExplorerConfig, RoiConfig


def test_roi():
    r = RoiConfig(west=130, south=32, east=131, north=33)
    assert r.is_set()