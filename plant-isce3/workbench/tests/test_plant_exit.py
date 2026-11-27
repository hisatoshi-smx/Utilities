from worklib.pipeline import plant_exit_code


def test_plant_exit_path_is_success():
    assert plant_exit_code("/tmp/out.tif") == 0


def test_plant_exit_int():
    assert plant_exit_code(1) == 1
    assert plant_exit_code(0) == 0