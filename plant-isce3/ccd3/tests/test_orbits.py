from datetime import datetime

from ccd3lib.orbits import find_existing


def test_find_existing_prefers_poeorb(tmp_path):
    names = [
        "S1D_OPER_AUX_POEORB_OPOD_20260726T071419_V20260705T225942_20260707T005942.EOF",
        "S1D_OPER_AUX_RESORB_OPOD_20260706T231000_V20260706T201000_20260706T232000.EOF",
        "S1A_OPER_AUX_POEORB_OPOD_20260726T071419_V20260705T225942_20260707T005942.EOF",
        "S1D_OPER_AUX_POEORB_OPOD_20260807T071418_V20260717T225942_20260719T005942.EOF",
    ]
    for n in names:
        (tmp_path / n).write_text("")
    t = datetime(2026, 7, 6, 20, 59, 39)
    assert find_existing(str(tmp_path), "S1D", t).endswith(names[0])
    (tmp_path / names[0]).unlink()
    assert find_existing(str(tmp_path), "S1D", t).endswith(names[1])
    assert find_existing(str(tmp_path), "S1C", t) is None
    assert find_existing(str(tmp_path), "S1D", datetime(2026, 8, 1)) is None
