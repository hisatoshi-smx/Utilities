import pytest

from worklib.outputs import safe_basename


def test_safe_basename_default():
    assert safe_basename("", "geocode_geocode.tif") == "geocode_geocode.tif"


def test_safe_basename_custom():
    assert safe_basename("saga_rtc.tif", "x.tif") == "saga_rtc.tif"


def test_safe_basename_blocks_path():
    with pytest.raises(ValueError):
        safe_basename("../../etc/passwd", "a.tif")