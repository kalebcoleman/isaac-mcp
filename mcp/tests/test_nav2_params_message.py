"""scripts/nav2_params.py: when it cannot find Nav2, the message names the usual cause."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "nav2_params.py"
spec = importlib.util.spec_from_file_location("nav2_params", SCRIPT)
nav2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nav2)


def test_not_found_says_to_source_the_script_and_not_in_a_pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AMENT_PREFIX_PATH", raising=False)
    assert nav2.main(["--out", str(tmp_path / "p.yaml")]) == 2
    err = capsys.readouterr().err
    assert "ros_env.sh" in err and "--source" in err and "pipeline" in err and "subshell" in err
    assert not (tmp_path / "p.yaml").exists()


def test_a_prefix_with_nav2_is_found(tmp_path, monkeypatch, capsys):
    params = tmp_path / "share" / "nav2_bringup" / "params"
    params.mkdir(parents=True)
    (params / "nav2_params.yaml").write_text("amcl:\n  ros__parameters:\n    base_frame_id: \"base_footprint\"\n")
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path))
    assert nav2.main(["--out", str(tmp_path / "p.yaml")]) == 0
    assert (tmp_path / "p.yaml").is_file()
