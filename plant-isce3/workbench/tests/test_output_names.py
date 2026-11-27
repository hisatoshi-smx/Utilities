from worklib.output_names import _direction_code, suggest_output_basenames


def test_suggest_nisar_names():
    info = {
        "path": "/data/NISAR_L1_PR_RSLC_023_125_A_018_4005_DHDH_A_20260622T203721_20260622T203756_P05023_N_F_J_001.h5",
        "platform": "NISAR",
        "datetime": "2026-06-22 20:37:21",
        "track": 125,
        "frame": 18,
        "direction": "Ascending",
    }
    names = suggest_output_basenames(info, frequency="A", polarization="HH", geocode_mode="rtc_gamma")
    assert names["geocode"].startswith("NISAR_tr125_fr18_fA_HH_20260622_A_")
    assert names["geocode"].endswith("_geocode_rtc_gamma.tif")
    assert "_rtc_radar.tif" in names["rtc"]


def test_direction_code_descending():
    assert _direction_code("Descending") == "D"
    assert _direction_code("Ascending") == "A"


def test_suggest_descending_in_filename():
    info = {
        "platform": "NISAR",
        "datetime": "2026-07-16 12:00:00",
        "track": 125,
        "direction": "Descending",
    }
    names = suggest_output_basenames(info, frequency="A", polarization="HH_HV", geocode_mode="rtc_gamma")
    assert "_D_geocode_rtc_gamma.tif" in names["geocode"]
    assert "_D_rtc_radar.tif" in names["rtc"]