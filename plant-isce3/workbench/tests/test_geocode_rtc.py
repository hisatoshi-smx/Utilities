from worklib.geocode_rtc import apply_rtc_geocode_argv


def test_rtc_presets_strip_gslc_and_add_gamma():
    base = ["in.h5", "--dem", "d.tif", "--gslc", "--terrain", "none", "--output", "out.tif"]
    argv = apply_rtc_geocode_argv(base, "HH")
    assert "--gslc" not in argv
    assert "--terrain" not in argv or "none" not in argv
    assert "--area-proj-gamma-naught" in argv
    assert "--list-of-polarizations" in argv
    ix = argv.index("--list-of-polarizations")
    assert argv[ix + 1] == "HH"