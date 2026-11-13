"""Tests for scripts/env.py and its run.sh integration (no interpreter is executed)."""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "env.py"

spec = importlib.util.spec_from_file_location("isaac_env_under_test", SCRIPT)
ie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ie)


def make_env(prefix, sim="6.2.0.0", lab=None, py="3.12", broken=False, link_python=False):
    """Create a fake venv-like prefix with an executable bin/python."""
    prefix = Path(prefix)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    python = prefix / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 99\n")  # must never run
    python.chmod(0o755)
    site = prefix / "lib" / f"python{py}" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    if sim:
        info = site / f"isaacsim-{sim}.dist-info"
        info.mkdir()
        if not broken:
            (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: isaacsim\nVersion: {sim}\n\nbody\n")
    if lab:
        info = site / f"isaaclab-{lab}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Name: isaaclab\nVersion: {lab}\n")
    return python


def make_standalone(root, version="5.1.0-rc.19+x", with_sh=True):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(version + "\n")
    if with_sh:
        (root / "python.sh").write_text("#!/bin/sh\n")
        (root / "python.sh").chmod(0o755)
    else:
        (root / "kit" / "python" / "bin").mkdir(parents=True)
        (root / "kit" / "python" / "bin" / "python3").write_text("x")
    return root


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A hermetic layout: <tmp>/ws/{repo,...} and a home containing '@'."""
    home = tmp_path / "me@example" / "home"
    home.mkdir(parents=True)
    ws = tmp_path / "ws"
    repo = ws / "twinsmith"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "env.py")
    shutil.copy(ROOT / "run.sh", repo / "run.sh")
    for var in ("TWINSMITH_SIM_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX",
                "TWINSMITH_WORKSPACE", "ISACSIM_PATH", "ISAACSIM_PATH", "ISAAC_PATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return type("W", (), {"home": home, "ws": ws, "repo": repo, "tmp": tmp_path})


def find(world, env=None, **kw):
    return ie.find_candidates(env or {}, home=world.home, repo=world.repo, system_roots=[], **kw)


def sources(cands):
    return [c["source"] for c in cands]


def test_pip_env_metadata_and_fields(world):
    py = make_env(world.repo / ".venv", sim="6.2.0.0", lab="3.0.0")
    [c] = find(world)
    assert c == {k: c[k] for k in c} and c["path"] == str(py)
    assert (c["kind"], c["isaacsim"], c["isaaclab"], c["python"], c["source"]) == (
        "pip", "6.2.0.0", "3.0.0", "3.12", "repo-venv")


def test_isaacsim_version_file_fallback(world):
    py = make_env(world.repo / ".venv", sim=None)
    pkg = world.repo / ".venv" / "lib" / "python3.12" / "site-packages" / "isaacsim"
    pkg.mkdir()
    (pkg / "VERSION").write_text("6.1.0.0\n")
    [c] = find(world)
    assert c["isaacsim"] == "6.1.0.0" and c["path"] == str(py)


def test_non_isaac_env_is_ignored(world):
    make_env(world.repo / ".venv", sim=None)
    assert find(world) == []


def test_broken_metadata_keeps_candidate_with_null_version(world):
    make_env(world.repo / ".venv", broken=True)
    [c] = find(world)
    assert c["isaacsim"] is None and c["kind"] == "pip"
    chosen, err = ie.select({}, [c])
    assert chosen is not None and err is None


@pytest.mark.parametrize("var", ["TWINSMITH_SIM_PYTHON"])
def test_explicit_env_rules(world, var):
    py = make_env(world.tmp / "elsewhere")
    [c] = find(world, {var: str(py)})
    assert c["source"] == f"env:{var}"


def test_the_explicit_interpreter_beats_a_higher_version(world):
    a = make_env(world.tmp / "a", sim="6.0.0.0")
    env = {"TWINSMITH_SIM_PYTHON": str(a)}
    assert sources(find(world, env))[0] == "env:TWINSMITH_SIM_PYTHON"
    chosen, _ = ie.select(env, find(world, env))
    assert chosen["path"] == str(a)


def test_explicit_env_pointing_nowhere_is_an_error(world):
    env = {"TWINSMITH_SIM_PYTHON": str(world.tmp / "nope" / "bin" / "python")}
    make_env(world.repo / ".venv")  # a perfectly good fallback must NOT be used
    chosen, err = ie.select(env, find(world, env))
    assert chosen is None and "TWINSMITH_SIM_PYTHON" in err


def test_workspace_envs_from_env_var_and_checkout_parent(world):
    py = make_env(world.tmp / "custom-ws" / "envs" / "sim")
    [c] = find(world, {"TWINSMITH_WORKSPACE": str(world.tmp / "custom-ws")})
    assert c["source"] == "workspace-envs" and c["path"] == str(py)
    # without the variable, the checkout's parent counts only when it has envs/
    assert find(world) == []
    py2 = make_env(world.ws / "envs" / "x")
    [c] = find(world)
    assert c["source"] == "workspace-envs" and c["path"] == str(py2)


def test_active_virtualenv_and_conda_prefix(world):
    v = make_env(world.tmp / "v")
    cp = make_env(world.tmp / "cp", sim="6.1.0.0")
    cands = find(world, {"VIRTUAL_ENV": str(world.tmp / "v"), "CONDA_PREFIX": str(world.tmp / "cp")})
    assert sources(cands) == ["env:VIRTUAL_ENV", "env:CONDA_PREFIX"]
    assert [c["path"] for c in cands] == [str(v), str(cp)]


def test_sibling_environments(world):
    a = make_env(world.ws / "other" / ".venv")
    b = make_env(world.ws / ".venv", sim="6.1.0.0")
    cands = find(world)
    assert sources(cands) == ["sibling-venv", "sibling-venv"]
    assert {c["path"] for c in cands} == {str(a), str(b)}


def test_conda_style_roots_under_home(world):
    paths = [make_env(world.home / root / "envs" / "e", sim=f"6.{i}.0.0")
             for i, root in enumerate(["miniconda3", "anaconda3", "miniforge3", "mambaforge", ".conda"])]
    cands = find(world)
    assert sources(cands) == ["conda-root"] * 5
    assert [c["path"] for c in cands] == [str(p) for p in paths]


def test_home_venv_folders(world):
    paths = [make_env(world.home / d / "e", sim=f"6.{i}.0.0") for i, d in enumerate(["venvs", ".virtualenvs", "envs"])]
    cands = find(world)
    assert sources(cands) == ["home-venvs"] * 3 and [c["path"] for c in cands] == [str(p) for p in paths]


def test_standalone_roots(world):
    make_standalone(world.home / "isaacsim", "5.0.0")
    make_standalone(world.home / "isaac-sim", "5.1.0", with_sh=False)
    make_standalone(world.home / ".local/share/ov/pkg/isaac-sim-4.5.0", "4.5.0")
    make_standalone(world.tmp / "viaenv", "6.0.0")
    env = {"ISAACSIM_PATH": str(world.tmp / "viaenv")}
    cands = find(world, env)
    assert [c["source"] for c in cands] == ["standalone:ISAACSIM_PATH", "standalone:home", "standalone:home",
                                            "standalone:ov-pkg"]
    assert cands[0]["kind"] == "standalone" and cands[0]["isaacsim"] == "6.0.0"
    assert cands[0]["path"].endswith("python.sh") and cands[0]["isaaclab"] is None
    assert cands[2]["path"].endswith("kit/python/bin/python3")
    assert cands[1]["isaacsim"] == "5.0.0"


def test_system_roots_parameter(world):
    root = make_standalone(world.tmp / "opt" / "isaac-sim-x", "5.1.0")
    [c] = ie.find_candidates({}, home=world.home, repo=world.repo, system_roots=[str(root)])
    assert c["source"] == "standalone:system"


def test_order_and_dedupe_by_resolved_path(world):
    py = make_env(world.repo / ".venv")
    link = world.tmp / "link-to-venv"
    link.symlink_to(world.repo / ".venv")
    env = {"VIRTUAL_ENV": str(link), "TWINSMITH_SIM_PYTHON": str(py)}
    cands = find(world, env)
    assert len(cands) == 1 and cands[0]["source"] == "env:TWINSMITH_SIM_PYTHON"
    other = make_env(world.home / "venvs" / "o", sim="6.1.0.0")
    assert sources(find(world, env)) == ["env:TWINSMITH_SIM_PYTHON", "home-venvs"] and other.exists()


def test_symlinked_venv_python(world):
    real_py = make_env(world.tmp / "real-venv")
    # bin/python inside a venv is typically a symlink to a system interpreter
    target = world.tmp / "system-python3.12"
    target.write_text("x")
    (world.tmp / "venv2" / "bin").mkdir(parents=True)
    (world.tmp / "venv2" / "bin" / "python").symlink_to(target)
    site = world.tmp / "venv2" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "isaacsim-6.2.0.0.dist-info").mkdir()
    (site / "isaacsim-6.2.0.0.dist-info" / "METADATA").write_text("Version: 6.2.0.0\n")
    [c] = find(world, {"VIRTUAL_ENV": str(world.tmp / "venv2")})
    assert c["isaacsim"] == "6.2.0.0" and c["path"] == str(world.tmp / "venv2" / "bin" / "python")
    # a python symlink living outside its venv is followed once
    outside = world.tmp / "bin-outside"
    outside.symlink_to(real_py)
    [c] = find(world, {"TWINSMITH_SIM_PYTHON": str(outside)})
    assert c["isaacsim"] == "6.2.0.0"


def test_selection_first_match_wins_and_version_only_filters(world):
    old = make_env(world.home / "venvs" / "a", sim="6.0.0.0")
    new1 = make_env(world.home / "venvs" / "b", sim="6.2.0.0")
    make_env(world.home / "venvs" / "c", sim="6.2.0.0")
    cands = find(world)
    chosen, _ = ie.select({}, cands)
    assert chosen["path"] == str(old)  # first match in search order, not the newest
    chosen, _ = ie.select({}, cands, require="6.0")
    assert chosen["path"] == str(old)
    chosen, err = ie.select({}, cands, require="5.1")
    assert chosen is None and "5.1" in err
    chosen, _ = ie.select({}, cands, require="6.2.0.0")
    assert chosen["path"] == str(new1)  # first of the matching ones


def test_a_workspace_env_is_not_overridden_by_a_newer_env_elsewhere(world, monkeypatch):
    """A newer unrelated project venv must not beat the workspace env."""
    ws = world.tmp / "workspace"
    mine = make_env(ws / "envs" / "sim-6.2", sim="6.2.0.0")
    make_env(world.home / "oldproj" / ".venv", sim="6.3.0.0")
    env = {"TWINSMITH_WORKSPACE": str(ws)}
    monkeypatch.setenv("TWINSMITH_WORKSPACE", str(ws))
    chosen, _ = ie.select(env, find(world, env))
    assert chosen["path"] == str(mine)
    chosen, _ = ie.select(env, find(world, env), require="6")
    assert chosen["path"] == str(mine)


def test_stale_dist_info_folders_report_the_highest_version(world):
    py = make_env(world.repo / ".venv", sim="6.2.0.0")
    site = world.repo / ".venv" / "lib" / "python3.12" / "site-packages"
    info = site / "isaacsim-10.0.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Name: isaacsim\nVersion: 10.0.0.0\n")
    [c] = find(world)
    assert c["path"] == str(py) and c["isaacsim"] == "10.0.0.0"


def test_selection_lab_preference(world):
    plain = make_env(world.home / "venvs" / "a", sim="6.2.0.0")
    lab = make_env(world.home / "venvs" / "b", sim="6.1.0.0", lab="3.0.0")
    cands = find(world)
    assert ie.select({}, cands)[0]["path"] == str(plain)
    assert ie.select({}, cands, lab=True)[0]["path"] == str(lab)
    assert ie.select({}, cands, require="6.2", lab=True)[0]["path"] == str(plain)


def run_cli(world, *args, env=None, script=None):
    e = {k: v for k, v in os.environ.items() if k not in
         ("TWINSMITH_SIM_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX",
          "TWINSMITH_WORKSPACE", "ISAACSIM_PATH", "ISAAC_PATH")}
    e["HOME"] = str(world.home)
    e.update(env or {})
    return subprocess.run([sys.executable, str(script or world.repo / "scripts" / "env.py"), *args],
                          capture_output=True, text=True, env=e, cwd=str(world.tmp))


def test_cli_resolve_python_stdout_only(world):
    py = make_env(world.home / "venvs" / "a")
    r = run_cli(world, "resolve-python")
    assert r.returncode == 0 and r.stdout == f"{py}\n" and r.stderr == ""


def test_cli_resolve_failure_is_one_line_on_stderr(world):
    r = run_cli(world, "resolve-python", "--require-version", "9.9")
    assert r.returncode == 1 and r.stdout == "" and len(r.stderr.strip().splitlines()) == 1


def test_cli_explicit_env(world):
    py = make_env(world.tmp / "pinned", sim="6.0.0.0")
    make_env(world.home / "venvs" / "a", sim="6.2.0.0")
    assert run_cli(world, "resolve-python", env={"TWINSMITH_SIM_PYTHON": str(py)}).stdout == f"{py}\n"
    r = run_cli(world, "resolve-python", env={"TWINSMITH_SIM_PYTHON": str(world.tmp / "missing")})
    assert r.returncode == 1 and r.stdout == ""


def test_cli_standalone_resolves_to_python_sh(world):
    root = make_standalone(world.tmp / "sa", "5.1.0")
    r = run_cli(world, "resolve-python", env={"ISAAC_PATH": str(root)})
    assert r.stdout == f"{root / 'python.sh'}\n"


def test_cli_find_json_and_table(world):
    make_env(world.home / "venvs" / "a", lab="3.0.0")
    data = json.loads(run_cli(world, "find", "--json").stdout)
    assert [d["isaaclab"] for d in data] == ["3.0.0"]
    assert set(data[0]) == {"path", "kind", "isaacsim", "isaaclab", "python", "source"}
    table = run_cli(world, "find").stdout
    assert "6.2.0.0" in table and "home-venvs" in table


def test_cli_where(world):
    make_env(world.home / "venvs" / "a")
    data = json.loads(run_cli(world, "where", "--json", env={"TWINSMITH_WORKSPACE": str(world.tmp / "w")}).stdout)
    assert data["workspace"] == str(world.tmp / "w") and data["interpreter"].endswith("bin/python")
    assert "home-venvs" in data["interpreter_why"]
    assert "interpreter:" in run_cli(world, "where").stdout


def test_init_creates_layout_idempotent_and_never_overwrites(world):
    target = world.tmp / "newws"
    r = run_cli(world, "init", "--workspace", str(target))
    assert r.returncode == 0 and f'export TWINSMITH_WORKSPACE="{target}"' in r.stdout
    assert "TWINSMITH_ASSET_ROOT" in r.stdout and "my-robot/v1" in r.stdout
    for d in ("assets", "envs", "projects", "runs", "tools"):
        assert (target / d).is_dir()
    assert not (target / "isaac-mcp-assets").exists() and 'TWINSMITH_ASSET_ROOT="' + str(target) + '/assets"' in r.stdout
    assert "Robotics workspace" in (target / "README.md").read_text()
    assert "Try twinsmith first" in (target / "AGENTS.md").read_text()
    assert (target / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    (target / "AGENTS.md").write_text("mine too")
    (target / "README.md").write_text("mine")
    (target / "assets" / "keep.txt").write_text("x")
    assert run_cli(world, "init", "--workspace", str(target)).returncode == 0
    assert (target / "README.md").read_text() == "mine" and (target / "assets" / "keep.txt").exists()
    assert (target / "AGENTS.md").read_text() == "mine too"


def test_init_does_not_create_group_writable_folders(world):
    """The asset store must not be writable by group/others."""
    old = os.umask(0o002)
    try:
        target = world.tmp / "umask-ws"
        assert run_cli(world, "init", "--workspace", str(target)).returncode == 0
    finally:
        os.umask(old)
    for d in ("assets", "envs", "projects", "runs", "tools"):
        assert (target / d).stat().st_mode & 0o022 == 0, d


def test_init_default_workspace_uses_env_then_home(world):
    r = run_cli(world, "init")
    assert r.returncode == 0 and (world.home / "robotics-workspace" / "runs").is_dir()
    assert not (world.home / "isaac-workspace").exists()
    r = run_cli(world, "init", env={"TWINSMITH_WORKSPACE": str(world.tmp / "viaenv")})
    assert (world.tmp / "viaenv" / "assets").is_dir()


def test_init_uses_the_old_home_folder_only_while_the_new_one_is_absent(world):
    old = world.home / "isaac-workspace"
    old.mkdir()
    (old / "keep.txt").write_text("x")
    assert run_cli(world, "init").returncode == 0
    assert (old / "runs").is_dir() and (old / "assets").is_dir() and (old / "keep.txt").read_text() == "x"
    assert not (world.home / "robotics-workspace").exists()
    (world.home / "robotics-workspace").mkdir()                 # the new folder now exists: it wins
    assert run_cli(world, "init").returncode == 0
    assert (world.home / "robotics-workspace" / "runs").is_dir()
    r = run_cli(world, "init", env={"TWINSMITH_WORKSPACE": str(world.tmp / "viaenv")})   # the variable wins over both
    assert (world.tmp / "viaenv" / "runs").is_dir() and r.returncode == 0


def test_init_leaves_an_existing_old_asset_folder_alone_and_says_so(world):
    target = world.tmp / "oldws"
    (target / "isaac-mcp-assets" / "pack" / "v1").mkdir(parents=True)
    (target / "isaac-mcp-assets" / "pack" / "v1" / "pack.yaml").write_text("{}")
    r = run_cli(world, "init", "--workspace", str(target))
    assert r.returncode == 0
    assert (target / "isaac-mcp-assets" / "pack" / "v1" / "pack.yaml").read_text() == "{}"
    assert not (target / "assets").exists()          # an empty assets/ would be preferred over the populated old folder
    assert "isaac-mcp-assets" in r.stdout and "left as it is" in r.stdout
    assert f'TWINSMITH_ASSET_ROOT="{target}/isaac-mcp-assets"' in r.stdout
    assert (target / "envs").is_dir() and (target / "tools").is_dir()


def test_init_refuses_workspace_inside_checkout(world):
    for sub in (world.repo / "ws", world.repo):
        r = run_cli(world, "init", "--workspace", str(sub))
        assert r.returncode == 2 and "checkout" in r.stderr
    assert not (world.repo / "ws").exists()
    link = world.tmp / "linkrepo"
    link.symlink_to(world.repo)
    assert run_cli(world, "init", "--workspace", str(link / "ws")).returncode == 2


def test_unsupported_platform(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert ie.main(["find"]) == 2
    assert "unsupported platform" in capsys.readouterr().err
    monkeypatch.setattr(sys, "platform", "win32")
    assert ie.main(["resolve-python"]) == 2


def run_sh(world, shim_body=None, env=None, args=(), path_dirs=None):
    shim = world.tmp / "shim"
    shim.mkdir(exist_ok=True)
    if shim_body is not None:
        (shim / "python3").write_text(shim_body)
        (shim / "python3").chmod(0o755)
    # keep coreutils reachable but without a real python3 ahead of the shim
    e = {"HOME": str(world.home), "PATH": f"{shim}:/usr/bin:/bin", "TWINSMITH_PRINT_PYTHON": "1"}
    e.update(env or {})
    return subprocess.run(["bash", str(world.repo / "run.sh"), *args], capture_output=True, text=True,
                          env=e, cwd=str(world.tmp))


def test_run_sh_autodetects_via_python3_shim(world):
    shim = f'#!/bin/sh\n[ "$2" = resolve-python ] && echo {world.tmp}/found/bin/python && exit 0\nexit 1\n'
    r = run_sh(world, shim)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{world.tmp}/found/bin/python\n"
    assert "auto-detected" in r.stderr and "TWINSMITH_SIM_PYTHON" in r.stderr


def test_run_sh_real_script_end_to_end(world):
    py = make_env(world.ws / "envs" / "sim")
    real = shutil.which("python3")
    r = run_sh(world, f'#!/bin/sh\nexec {real} "$@"\n')
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{py}\n"


def test_run_sh_explicit_names_and_venv_keep_old_behaviour(world):
    shim = "#!/bin/sh\necho SHOULD-NOT-BE-CALLED >&2\nexit 1\n"
    r = run_sh(world, shim, env={"TWINSMITH_SIM_PYTHON": "/old/alias"})
    assert r.stdout == "/old/alias\n" and "auto-detected" not in r.stderr and "SHOULD-NOT" not in r.stderr
    r = run_sh(world, shim, env={"TWINSMITH_SIM_PYTHON": "/new/name"})
    assert r.stdout == "/new/name\n"
    make_env(world.repo / ".venv")
    r = run_sh(world, shim)
    assert r.stdout == f"{world.repo}/.venv/bin/python\n" and "SHOULD-NOT" not in r.stderr


def test_run_sh_nothing_found_keeps_error_with_pointer(world):
    shim = "#!/bin/sh\nexit 1\n"
    r = run_sh(world, shim, env={"TWINSMITH_PRINT_PYTHON": "0"})
    assert r.returncode == 1
    assert "env.py find" in r.stderr and "LINUX-SETUP.md" in r.stderr and "TWINSMITH_SIM_PYTHON" in r.stderr


def test_run_sh_without_python3_still_reports(world):
    # a PATH with only dirname/bash and no python3: falls back to the old default path
    bindir = world.tmp / "minbin"
    bindir.mkdir()
    for tool in ("dirname", "bash"):
        (bindir / tool).symlink_to(shutil.which(tool))
    e = {"HOME": str(world.home), "PATH": str(bindir), "TWINSMITH_PRINT_PYTHON": "1"}
    r = subprocess.run([str(bindir / "bash"), str(world.repo / "run.sh")], capture_output=True, text=True,
                       env=e, cwd=str(world.tmp))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith(".venv/bin/python")


def test_project_venvs_one_and_two_levels_below_home_are_found(world):
    one = make_env(world.home / "myproject" / ".venv", sim="6.2.0.0")
    two = make_env(world.home / "group" / "robot" / ".venv", sim="6.1.0.0", lab="3.0.0")
    cands = find(world)
    assert sorted(c["path"] for c in cands) == sorted([str(one), str(two)])
    assert set(sources(cands)) == {"project-venv"}


def test_hidden_folders_and_deeper_venvs_are_not_scanned(world):
    make_env(world.home / ".cache" / "x" / ".venv", sim="6.2.0.0")
    make_env(world.home / "a" / "b" / "c" / ".venv", sim="6.2.0.0")
    assert find(world) == []


def test_project_venv_rule_ranks_below_the_conventional_rules(world):
    make_env(world.home / "proj" / ".venv", sim="6.2.0.0")
    repo_env = make_env(world.repo / ".venv", sim="6.2.0.0")
    first = find(world)[0]
    assert first["path"] == str(repo_env) and first["source"] == "repo-venv"


def test_run_sh_autodetects_when_stderr_is_a_socket(world):
    # Opening /dev/stderr fails on a socket (journald, some CI), which used to lose the detected interpreter.
    shim = world.tmp / "shim"
    shim.mkdir(exist_ok=True)
    (shim / "python3").write_text(f'#!/bin/sh\necho {world.tmp}/found/bin/python\n')
    (shim / "python3").chmod(0o755)
    env = {"HOME": str(world.home), "PATH": f"{shim}:/usr/bin:/bin", "TWINSMITH_PRINT_PYTHON": "1"}
    ours, theirs = socket.socketpair()
    with ours, theirs:
        r = subprocess.run(["bash", str(world.repo / "run.sh")], stdout=subprocess.PIPE, stderr=theirs,
                           text=True, env=env, cwd=str(world.tmp))
    assert r.returncode == 0
    assert r.stdout == f"{world.tmp}/found/bin/python\n"
