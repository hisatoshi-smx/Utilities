from worklib.catalog import TOOLS, tool_schema


def test_geocode_schema():
    s = tool_schema("geocode")
    assert s["tool"] == "geocode"
    assert len(s["fields"]) > 20
    assert "input_file" in s["pinned"]


def test_rtc_schema():
    assert "rtc" in TOOLS
    assert any(f["dest"] == "simulate" for f in tool_schema("rtc")["fields"])