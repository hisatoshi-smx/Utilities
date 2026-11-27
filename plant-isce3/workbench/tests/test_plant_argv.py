from worklib.plant_argv import prepare_plant_argv


def test_strip_log_conflict():
    argv = prepare_plant_argv(["in.h5", "--log-enabled", "--text-color", "--output", "o.tif"])
    assert "--log-enabled" not in argv
    assert "--text-color" not in argv
    assert "--no-text-color" in argv
    assert "--no-log" in argv