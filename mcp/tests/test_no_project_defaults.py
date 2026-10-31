from pathlib import Path


def test_python_sources_have_no_cross_project_default_paths():
    sources = Path(__file__).resolve().parents[1] / "isaac_mcp"
    forbidden = ("ur5e-digital-twin", "/Desktop/")
    violations = [str(path.relative_to(sources)) for path in sources.rglob("*.py")
                  if any(value in path.read_text(encoding="utf-8") for value in forbidden)]
    assert not violations, violations
