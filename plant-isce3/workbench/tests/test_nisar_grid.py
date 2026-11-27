import os

import pytest

from worklib.config import ExplorerConfig, GridConfig, RoiConfig


def test_resolve_manual_spacing():
    from worklib.nisar_grid import resolve_spacing_m

    cfg = ExplorerConfig(name="t", grid=GridConfig(spacing=12.5), roi=RoiConfig(west=1, south=2, east=3, north=4))
    sp, note = resolve_spacing_m(cfg)
    assert sp == 12.5
    assert "手動" in note


@pytest.mark.skipif(not os.environ.get("NISAR_TEST_H5"), reason="NISAR_TEST_H5 未設定")
def test_resolve_auto_spacing():
    from worklib.nisar_grid import resolve_spacing_m

    cfg = ExplorerConfig(
        name="t",
        input_h5=os.environ["NISAR_TEST_H5"],
        frequency="A",
        grid=GridConfig(spacing=None),
        roi=RoiConfig(west=129, south=32, east=131, north=34),
    )
    sp, note = resolve_spacing_m(cfg)
    assert 2 < sp < 20
    assert "自動" in note