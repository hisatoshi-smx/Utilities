import pytest

from worklib.geocode_rtc import apply_rtc_geocode_argv
from worklib.polarizations import normalize_mode, polarizations_for, split_output_per_pol


def test_pols_list():
    assert polarizations_for("HH") == ["HH"]
    assert polarizations_for("HH_HV") == ["HH", "HV"]


def test_rtc_argv_one_pol():
    argv = apply_rtc_geocode_argv(["in.h5", "--output", "o.tif"], "HV")
    i = argv.index("--list-of-polarizations")
    assert argv[i + 1] == "HV"
    assert "--no-log" not in argv


def test_rtc_argv_both():
    argv = apply_rtc_geocode_argv(["in.h5", "--output", "o.tif"], "HH_HV")
    i = argv.index("--list-of-polarizations")
    assert argv[i + 1 : i + 3] == ["HH", "HV"]


def test_split_output():
    assert split_output_per_pol("/out/foo.tif", "HV").endswith("_HV.tif")


def test_normalize_invalid():
    with pytest.raises(ValueError):
        normalize_mode("VV")