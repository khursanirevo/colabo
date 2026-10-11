"""Claude Code statusline segment showing Colab session + compute-unit balance.

    colabo statusline            # print the segment (reads Claude's JSON on stdin, optional)
    colabo statusline install    # wrap the current statusLine in ~/.claude/settings.json
    colabo statusline uninstall  # restore the previous statusLine
    colabo statusline --refresh  # (internal) refresh the usage cache via `colab usage`

The segment must render in a few milliseconds, but `colab usage` takes ~3s, so the
balance comes from a cache that is refreshed by a detached background process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .cli import ColaboError, Project, colab_bin, home, info

CACHE_TTL = 120  # seconds between `colab usage` refreshes
REFRESH_LOCK_TTL = 30  # do not spawn another refresh within this window
WRAPPER_NAME = "colabo-statusline.sh"
WRAPPER_MARK = "# colabo statusline wrapper"


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", home() / ".cache"))
    d = base / "colabo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def usage_cache() -> Path:
    return cache_dir() / "usage.json"


def settings_path() -> Path:
    return home() / ".claude" / "settings.json"


def wrapper_path() -> Path:
    return home() / ".claude" / WRAPPER_NAME


# --------------------------------------------------------------------------- usage cache
def parse_usage(text: str) -> dict:
    """Parse `colab usage` output: balance, hourly rate, active assignments."""
    out: dict = {}
    if m := re.search(r"Current balance:\s*([\d.]+)", text):
        out["balance"] = float(m.group(1))
    if m := re.search(r"Usage rate:\s*([\d.]+)", text):
        out["rate"] = float(m.group(1))
    if m := re.search(r"Active assignments:\s*(\d+)", text):
        out["active"] = int(m.group(1))
    return out


def refresh_usage() -> int:
    """Run `colab usage` and store the result (or the error) in the cache."""
    data: dict = {"ts": time.time()}
    try:
        r = subprocess.run([colab_bin(), "usage"], check=False, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            data.update(parse_usage(r.stdout))
        else:
            data["error"] = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["colab usage failed"]
    except (ColaboError, subprocess.TimeoutExpired, OSError) as e:
        data["error"] = [str(e).splitlines()[0]]
    tmp = usage_cache().with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(usage_cache())
    return 0


def load_usage() -> dict:
    try:
        return json.loads(usage_cache().read_text())
    except (OSError, ValueError):
        return {}


def maybe_spawn_refresh(data: dict) -> None:
    """Kick off a detached refresh if the cache is stale and none is in flight."""
    if time.time() - data.get("ts", 0) < CACHE_TTL:
        return
    lock = cache_dir() / "refresh.lock"
    try:
        if time.time() - lock.stat().st_mtime < REFRESH_LOCK_TTL:
            return
    except OSError:
        pass
    lock.touch()
    exe = shutil.which("colabo")
    cmd = [exe, "statusline", "--refresh"] if exe else [sys.executable, "-m", "colabo", "statusline", "--refresh"]
    subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


# --------------------------------------------------------------------------- session info
def load_sessions() -> dict:
    try:
        return json.loads((home() / ".config" / "colab-cli" / "sessions.json").read_text())
    except (OSError, ValueError):
        return {}


def project_for(cwd: str | None) -> Project | None:
    if not cwd:
        return None
    try:
        return Project.load(Path(cwd))
    except (ColaboError, OSError, KeyError):
        return None


def session_label(cwd: str | None) -> str:
    """'work·T4 ⇅' for the project's session, 'off' when nothing is running."""
    sessions = load_sessions()
    proj = project_for(cwd)
    name = proj.session if proj else None
    if name and name in sessions:
        sess = sessions[name]
    elif sessions and not name:
        name, sess = next(iter(sessions.items()))
    else:
        return "off"
    acc = str(sess.get("accelerator", "")).upper()
    label = name if acc in ("", "NONE") else f"{name}·{acc}"
    if proj and (proj.root / "mutagen.yml.lock").exists():
        label += " ⇅"
    return label


def segment(stdin_json: str = "") -> str:
    cwd = None
    if stdin_json.strip():
        try:
            d = json.loads(stdin_json)
            cwd = d.get("cwd") or (d.get("workspace") or {}).get("current_dir")
        except ValueError:
            pass
    label = session_label(cwd)
    parts = [f"⚡colab {label}"]
    data = load_usage()
    maybe_spawn_refresh(data)
    if "balance" in data:
        parts.append(f"{data['balance']:.1f} CU")
        # Google keeps reporting the old rate for a while after a VM is released; only show it with a live session.
        if data.get("rate") and label != "off":
            parts.append(f"{data['rate']:.2f}/hr")
    elif "error" in data:
        parts.append("usage?")
    return " · ".join(parts)


# --------------------------------------------------------------------------- install
def wrapper_script(prev_cmd: str, colabo_exe: str) -> str:
    return f"""#!/bin/sh
{WRAPPER_MARK} (managed by `colabo statusline install`; `colabo statusline uninstall` restores PREV)
PREV={json.dumps(prev_cmd)}
input=$(cat)
prev=""
if [ -n "$PREV" ]; then
  prev=$(printf '%s' "$input" | sh -c "$PREV" 2>/dev/null)
fi
seg=$(printf '%s' "$input" | {json.dumps(colabo_exe)} statusline 2>/dev/null)
if [ -z "$prev" ]; then
  printf '%s\\n' "$seg"
elif [ -z "$seg" ]; then
  printf '%s\\n' "$prev"
else
  printf '%s\\n' "$prev" | awk -v s="$seg" 'NR==1 {{ $0 = $0 " | " s }} {{ print }}'
fi
"""


def _read_settings() -> dict:
    p = settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except ValueError as e:
        raise ColaboError(f"{p} is not valid JSON ({e}); fix it before installing the statusline.") from e


def _write_settings(data: dict) -> None:
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_name("settings.json.bak-colabo"))
    p.write_text(json.dumps(data, indent=2) + "\n")


def _prev_from_wrapper() -> str:
    try:
        m = re.search(r'^PREV=(".*")$', wrapper_path().read_text(), re.MULTILINE)
        return json.loads(m.group(1)) if m else ""
    except (OSError, ValueError):
        return ""


def install() -> int:
    settings = _read_settings()
    current = settings.get("statusLine") or {}
    wp = wrapper_path()
    if current.get("type") == "command" and current.get("command") in (str(wp), f"~/.claude/{WRAPPER_NAME}"):
        prev = _prev_from_wrapper()  # already installed: keep the original command
    elif current.get("type") == "command":
        prev = current.get("command", "")
    else:
        prev = ""
    exe = shutil.which("colabo") or f"{sys.executable} -m colabo"
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(wrapper_script(prev, exe))
    wp.chmod(0o755)
    settings["statusLine"] = {"type": "command", "command": str(wp)}
    _write_settings(settings)
    info(f"statusline installed -> {wp}" + (f" (chained after: {prev})" if prev else ""))
    return 0


def uninstall() -> int:
    settings = _read_settings()
    prev = _prev_from_wrapper()
    if prev:
        settings["statusLine"] = {"type": "command", "command": prev}
    else:
        settings.pop("statusLine", None)
    _write_settings(settings)
    wrapper_path().unlink(missing_ok=True)
    info("statusline removed" + (f"; restored: {prev}" if prev else ""))
    return 0


def main(args) -> int:
    if args.refresh:
        return refresh_usage()
    if args.action == "install":
        return install()
    if args.action == "uninstall":
        return uninstall()
    stdin = "" if sys.stdin.isatty() else sys.stdin.read()
    print(segment(stdin))
    return 0
