import pytest
from pydantic import ValidationError

from ccd3lib.config import ProjectConfig, load_config, save_config, step_signatures
from ccd3lib.projects import Project, ProjectError, create_project, list_projects, validate_name


def test_defaults_roundtrip(tmp_path):
    cfg = ProjectConfig(name="demo")
    p = tmp_path / "project.yaml"
    save_config(cfg, str(p))
    assert load_config(str(p)) == cfg


@pytest.mark.parametrize(
    "patch",
    [
        {"ccd": {"window_x": 4}},
        {"roi": {"west": 1.0, "south": 0.0, "east": 0.5, "north": 1.0}},
        {"roi": {"west": 1.0}},
        {"inputs": {"scenes": ["a", "b"]}},
        {"grid": {"spacing": -1}},
        {"unknown": 1},
    ],
)
def test_invalid(patch):
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({"name": "x", **patch})


def test_signature_cascade():
    a = ProjectConfig(name="x")
    b = a.model_copy(deep=True)
    b.ccd.window_x = 9
    sa, sb = step_signatures(a), step_signatures(b)
    for s in ("inspect", "orbit", "dem", "grid", "geocode"):
        assert sa[s] == sb[s]
    assert sa["coherence"] != sb["coherence"]
    assert sa["products"] != sb["products"]

    c = a.model_copy(deep=True)
    c.grid.spacing = 20
    sc = step_signatures(c)
    assert sc["dem"] == sa["dem"]
    assert sc["grid"] != sa["grid"] and sc["geocode"] != sa["geocode"]


@pytest.mark.parametrize("name", ["ok_1", "能登2024", "a.b-c"])
def test_valid_names(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "..", "../x", "a/b", ".hidden", "-x", "a b", "x" * 65])
def test_invalid_names(name):
    with pytest.raises(ProjectError):
        validate_name(name)


def test_create_and_isolation(tmp_path):
    root = str(tmp_path)
    p1 = create_project("p1", "first", root=root)
    p2 = create_project("p2", root=root)
    assert p1.root != p2.root
    for p in (p1, p2):
        for d in ("work", "output", "logs"):
            assert (tmp_path / p.name / d).is_dir()
    with pytest.raises(ProjectError):
        create_project("p1", root=root)
    names = [x["name"] for x in list_projects(root)]
    assert names == ["p1", "p2"]
    assert Project("p1", root).load().description == "first"
