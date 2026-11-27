import os

import pytest


@pytest.mark.skipif(
    not os.environ.get("NISAR_TEST_H5"),
    reason="NISAR_TEST_H5 が未設定",
)
def test_inspect_nisar_live():
    from worklib.inspect import inspect_nisar

    info = inspect_nisar(os.environ["NISAR_TEST_H5"])
    assert info["footprint"]
    assert info["datetime"]


def test_format_zd_time():
    from worklib.inspect import _format_zd_time

    class I:
        zdStartTime = "2026-07-16T20:37:12.123456789"

    assert _format_zd_time(I()) == "2026-07-16 20:37:12"