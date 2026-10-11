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


# --------------------------------------------------------------------------- statusline
import json  # noqa: E402

from colabo import statusline  # noqa: E402


def _settings(home):
    return json.loads((home / ".claude" / "settings.json").read_text())


def test_statusline_install_chains_and_is_idempotent(env):
    home, _ = env
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"model": "x", "statusLine": {"type": "command", "command": "echo PREVBAR"}})
    )
    assert statusline.install() == 0
    wrapper = home / ".claude" / "colabo-statusline.sh"
    assert _settings(home)["statusLine"]["command"] == str(wrapper)
    assert _settings(home)["model"] == "x", "other settings preserved"
    assert 'PREV="echo PREVBAR"' in wrapper.read_text()
    statusline.install()  # second install must keep the ORIGINAL command, not wrap the wrapper
    assert 'PREV="echo PREVBAR"' in wrapper.read_text()
    assert statusline.uninstall() == 0
    assert _settings(home)["statusLine"] == {"type": "command", "command": "echo PREVBAR"}
    assert not wrapper.exists()


def test_statusline_install_without_previous(env):
    home, _ = env
    assert statusline.install() == 0
    assert 'PREV=""' in (home / ".claude" / "colabo-statusline.sh").read_text()
    statusline.uninstall()
    assert "statusLine" not in _settings(home)


def test_statusline_wrapper_appends_to_first_line(env, tmp_path):
    home, _ = env
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "printf 'line1\\nline2\\n'"}})
    )
    fake = tmp_path / "bin" / "colabo"
    fake.write_text("#!/bin/sh\necho SEG\n")
    fake.chmod(0o755)
    statusline.install()
    out = subprocess.run(
        ["sh", str(home / ".claude" / "colabo-statusline.sh")], input="{}", check=False, capture_output=True, text=True
    )
    assert out.stdout == "line1 | SEG\nline2\n"


def test_segment_formats_session_and_usage(env, monkeypatch):
    home, proj = env
    monkeypatch.setattr(statusline, "maybe_spawn_refresh", lambda data: None)
    assert statusline.segment("") == "⚡colab off"
    statusline.usage_cache().write_text(json.dumps({"ts": 1e12, "balance": 3.0, "rate": 1.07}))
    assert statusline.segment("") == "⚡colab off · 3.0 CU", "no rate shown without a live session"
    cli.main(["init", "--session", "work", "--no-claude"])
    (home / ".config" / "colab-cli").mkdir(parents=True)
    (home / ".config" / "colab-cli" / "sessions.json").write_text(json.dumps({"work": {"accelerator": "T4"}}))
    (proj / "mutagen.yml.lock").write_text("")
    statusline.usage_cache().write_text(json.dumps({"ts": 1e12, "balance": 12.34, "rate": 1.07, "active": 1}))
    assert statusline.segment(json.dumps({"cwd": str(proj)})) == "⚡colab work·T4 ⇅ · 12.3 CU · 1.07/hr"
    statusline.usage_cache().write_text(json.dumps({"ts": 1e12, "error": ["401"]}))
    assert statusline.segment(json.dumps({"cwd": "/nowhere"})) == "⚡colab work·T4 · usage?"


def test_parse_usage():
    assert statusline.parse_usage(
        "Current balance: 5.50 compute units\nUsage rate: 1.07/hr\nActive assignments: 1"
    ) == {
        "balance": 5.5,
        "rate": 1.07,
        "active": 1,
    }


def test_init_installs_statusline_unless_disabled(env):
    home, _ = env
    cli.main(["init", "--no-statusline"])
    assert not (home / ".claude" / "colabo-statusline.sh").exists()
    cli.main(["init"])
    assert (home / ".claude" / "colabo-statusline.sh").exists()
    assert _settings(home)["statusLine"]["command"].endswith("colabo-statusline.sh")
