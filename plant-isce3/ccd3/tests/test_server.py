import http.client
import json
import threading

import pytest

from ccd3lib import projects, server


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "PROJECTS_DIR", str(tmp_path))
    # create_project / list_projects / Project の既定ルートを差し替える
    orig_init = projects.Project.__init__

    def init(self, name, root=None):
        orig_init(self, name, root or str(tmp_path))

    monkeypatch.setattr(projects.Project, "__init__", init)
    s = server.make_server("127.0.0.1", 0)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s.server_address[1]
    s.shutdown()
    s.server_close()


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def test_project_flow(srv):
    st, d = req(srv, "GET", "/api/info")
    assert st == 200 and len(d["steps"]) == 7
    st, d = req(srv, "POST", "/api/projects", {"name": "case1", "description": "テスト"})
    assert st == 200 and d["name"] == "case1"
    st, d = req(srv, "POST", "/api/projects", {"name": "case1"})
    assert st == 400
    st, d = req(srv, "GET", "/api/projects/case1")
    assert st == 200 and d["config"]["name"] == "case1"
    cfg = d["config"]
    cfg["roi"] = {"west": 136.8, "south": 37.3, "east": 137.0, "north": 37.5}
    cfg["ccd"]["window_x"] = 9
    st, d = req(srv, "POST", "/api/projects/case1/config", {"config": cfg})
    assert st == 200 and d["config"]["ccd"]["window_x"] == 9
    assert any("シーン" in e for e in d["check"]["errors"])
    cfg["ccd"]["window_x"] = 8
    st, d = req(srv, "POST", "/api/projects/case1/config", {"config": cfg})
    assert st == 400 and d["details"][0]["field"] == "ccd.window_x"
    st, d = req(srv, "GET", "/api/projects")
    assert [p["name"] for p in d["projects"]] == ["case1"]
    st, d = req(srv, "POST", "/api/projects/case1/run", {})
    assert st == 200  # 起動はする（ワーカーが設定エラーで終了する）


def test_guards(srv):
    st, _ = req(srv, "POST", "/api/projects", None, {"Content-Type": "text/plain", "Content-Length": "2"})
    assert st == 415
    st, _ = req(srv, "GET", "/api/info", headers={"Host": "evil.example.com"})
    assert st == 403
    st, _ = req(srv, "GET", "/ccd3-static/../ccd3lib/server.py")
    assert st == 404
    st, _ = req(srv, "GET", "/api/projects/..%2F..%2Fetc")
    assert st == 404
    st, body = req(srv, "GET", "/")
    assert st == 200 and b"leaflet" in body
    st, _ = req(srv, "GET", "/ccd3-static/vendor/leaflet/leaflet.js")
    assert st == 200
