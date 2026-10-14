"""#1: `colabo down` must not silently destroy files the sync excludes.

The exclusions (`*.pt`, `*.safetensors`, `checkpoints/**`, `data/**`) are a correct sync default and a
silent data-loss default at teardown: they are chosen at `init` and they bite at `down`, possibly days
later, in a different session. A real pretrained checkpoint was lost to this on 2026-10-14 — every step
reported success, the CSVs synced, and only the artifact that cost the compute was missing.
"""
from __future__ import annotations

import textwrap
import types

import pytest

from colabo import cli


def _project(tmp_path, patterns=("*.pt", "*.safetensors", "checkpoints/**")):
    body = "\n".join(f'        - "{p}"' for p in patterns)
    (tmp_path / "mutagen.yml").write_text(
        textwrap.dedent(
            """\
            sync:
              defaults:
                ignore:
                  vcs: true
                  paths:
                    - "__pycache__"
                    - "*.pyc"
            """
        )
        + body
        + "\n"
    )
    p = types.SimpleNamespace(
        root=tmp_path, name="demo", session="demo", remote="/content/demo",
        ssh_target="colab-demo", host="colab-demo",
    )
    return p


def test_exclusions_are_read_from_the_project_config_and_noise_is_dropped(tmp_path):
    p = _project(tmp_path)
    got = cli.sync_exclusions(p)
    assert "*.pt" in got and "checkpoints/**" in got
    assert "__pycache__" not in got and "*.pyc" not in got, "build noise would spam every teardown"


def test_no_config_means_no_check(tmp_path):
    p = _project(tmp_path)
    (tmp_path / "mutagen.yml").unlink()
    assert cli.sync_exclusions(p) == []


def test_unreachable_vm_never_blocks_teardown(tmp_path, monkeypatch):
    """This is a safety net. If it cannot reach the VM it must get out of the way, not wedge the teardown."""
    p = _project(tmp_path)
    monkeypatch.setattr(cli.clog, "sh", lambda *a, **k: types.SimpleNamespace(returncode=255, stdout=""))
    assert cli.unsynced_remote_files(p, ["*.pt"]) == []


def test_found_files_are_reported_largest_first(tmp_path, monkeypatch, capsys):
    p = _project(tmp_path)
    monkeypatch.setattr(
        cli.clog, "sh",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="13421773\tresults/base6/model.pt\n430000000\tcheckpoints/e3.safetensors\n"
        ),
    )
    found = cli.unsynced_remote_files(p, ["*.pt", "checkpoints/**"])
    assert [f[0] for f in found] == ["checkpoints/e3.safetensors", "results/base6/model.pt"]
    n = cli.warn_unsynced(p)
    assert n == 2
    err = capsys.readouterr().err
    assert "will be DESTROYED" in err
    assert "model.pt" in err and "colab download" in err
    assert "--force" in err, "the user must be told how to proceed deliberately"


def test_empty_result_is_silent(tmp_path, monkeypatch, capsys):
    p = _project(tmp_path)
    monkeypatch.setattr(cli.clog, "sh", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=""))
    assert cli.warn_unsynced(p) == 0
    assert capsys.readouterr().err == "", "the common case must stay quiet"
