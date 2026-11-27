from worklib.pipeline import _tool_run_label
from worklib.config import ExplorerConfig, GeocodeConfig


def test_tool_run_label():
    cfg = ExplorerConfig.model_validate(
        {"name": "t", "geocode": {"output": "rtc_gamma"}}
    )
    assert _tool_run_label("geocode", cfg) == "geocode（RTC 付き γ⁰）"
    cfg = cfg.model_copy(update={"geocode": GeocodeConfig(output="complex_gslc")})
    assert _tool_run_label("geocode", cfg) == "geocode（複素 GSLC）"
    assert _tool_run_label("rtc", cfg) == "rtc"