"""Offline tests: `colabo init` output and config handling, with fake `colab`/`mutagen` on PATH."""

import os
import stat
import subprocess
import sys

import pytest

from colabo import cli


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated HOME with an ed25519 key, a fake `colab` binary, and an empty project dir."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_ed25519").write_text("fake")
    (home / ".ssh" / "config").write_text("Host other\n  User me\n\nHost *\n  ServerAliveInterval 60\n")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for name in ("colab", "mutagen", "curl"):
        f = fakebin / name
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    proj = tmp_path / "My Project"
    proj.mkdir()
    monkeypatch.chdir(proj)
    return home, proj


def test_init_writes_everything(env):
    home, proj = env
    assert cli.main(["init", "--gpu", "L4"]) == 0

    cfg = (proj / "colabo.toml").read_text()
    assert 'name = "my-project"' in cfg and 'accelerator = "L4"' in cfg

    ssh = (home / ".ssh" / "config").read_text()
    assert "Host colab-my-project" in ssh
    assert "--proxy-mode -s my-project --gpu L4" in ssh
    assert "ControlMaster auto" in ssh
    assert ssh.index("colab-my-project") < ssh.index("Host *"), "block must precede the global Host *"
    assert "Host other" in ssh, "existing entries preserved"

    my = (proj / "mutagen.yml").read_text()
    assert 'beta: "colab-my-project:/content/my-project"' in my
    assert "<!-- >>> colabo >>>" in (proj / "CLAUDE.md").read_text()
    assert "mutagen.yml.lock" in (proj / ".gitignore").read_text()
    assert (home / ".claude" / "skills" / "colabo" / "SKILL.md").exists()


def test_init_is_idempotent(env):
    home, proj = env
    cli.main(["init"])
    cli.main(["init", "--gpu", "A100"])
    ssh = (home / ".ssh" / "config").read_text()
    assert ssh.count("Host colab-my-project") == 1
    assert "--gpu A100" in ssh and "--gpu T4" not in ssh
    assert (proj / "CLAUDE.md").read_text().count("<!-- >>> colabo >>>") == 1


def test_project_load_walks_up(env):
    _, proj = env
    cli.main(["init", "--session", "work"])
    sub = proj / "a" / "b"
    sub.mkdir(parents=True)
    p = cli.Project.load(sub)
    assert p.session == "work" and p.host == "colab-work" and p.root == proj.resolve()


def test_bad_accelerator_rejected(env):
    assert cli.main(["init", "--gpu", "RTX9090"]) == 2


def test_missing_key_is_actionable(env, tmp_path):
    home, _ = env
    (home / ".ssh" / "id_ed25519").unlink()
    with pytest.raises(cli.ColaboError, match="ssh-keygen -t ed25519"):
        cli.ssh_key()


def test_console_script_help():
    out = subprocess.run([sys.executable, "-m", "colabo.cli", "--help"], check=False, capture_output=True, text=True)
    assert out.returncode == 0 and "colabo" in out.stdout
