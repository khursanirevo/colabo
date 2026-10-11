"""`colabo feedback`: file a GitHub issue against colabo with diagnostics attached.

    colabo feedback "run hangs after Ctrl-C" -m "what happened / what I expected"
    colabo feedback --web            # open the prefilled issue form in the browser instead
    colabo feedback --print          # just print the report (no GitHub)

Only error/warning lines from the colab CLI log are included, with tokens redacted.
"""

from __future__ import annotations

import contextlib
import io
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

from .cli import CONFIG_FILE, Project, __version__, colab_bin, home, info

REPO = "khursanirevo/colabo"
REDACT = re.compile(r"(Bearer\s+|ya29\.|token=|pypi-|refresh_token\W+)\S+", re.IGNORECASE)
LOG_LINE = re.compile(r" - (ERROR|WARNING|CRITICAL) - |Traceback|Error:", re.IGNORECASE)


def _ver(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "?"
    except (OSError, subprocess.TimeoutExpired):
        return "not installed"


def recent_log_problems(n: int = 15) -> list[str]:
    log = home() / ".config" / "colab-cli" / "colab.log"
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return []
    hits = [REDACT.sub(r"\1<redacted>", ln)[:300] for ln in lines if LOG_LINE.search(ln)]
    return hits[-n:]


def doctor_output() -> str:
    from .cli import cmd_doctor

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            cmd_doctor(None)
        except Exception as e:
            buf.write(f"(doctor failed: {e})\n")
    return buf.getvalue().strip()


def build_report(message: str) -> str:
    try:
        colab = _ver([colab_bin(), "version"])
    except Exception:
        colab = "not installed"
    proj_cfg = ""
    try:
        p = Project.load()
        proj_cfg = (p.root / CONFIG_FILE).read_text().strip()
    except Exception:
        proj_cfg = "(not inside a colabo project)"
    log = recent_log_problems()
    parts = [
        "## What happened / what I expected",
        message.strip() or "_(fill in)_",
        "",
        "## Environment",
        "```",
        f"colabo {__version__}",
        f"colab CLI: {colab}",
        f"mutagen: {_ver(['mutagen', 'version'])}",
        f"os: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        "```",
        "",
        "## colabo doctor",
        "```",
        doctor_output(),
        "```",
        "",
        f"## {CONFIG_FILE}",
        "```toml",
        proj_cfg,
        "```",
    ]
    if log:
        parts += ["", "## Recent colab CLI errors/warnings (redacted)", "```", *log, "```"]
    return "\n".join(parts) + "\n"


def main(args) -> int:
    title = " ".join(args.title).strip() or "feedback: (describe in one line)"
    body = build_report(args.message or "")
    if args.print:
        print(f"# {title}\n\n{body}")
        return 0
    if not shutil.which("gh"):
        url = f"https://github.com/{REPO}/issues/new?" + urllib.parse.urlencode({"title": title, "body": body})
        print(body)
        info("`gh` CLI not found. Open this URL to file the issue (body above is prefilled):")
        print(url)
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = Path(f.name)
    cmd = ["gh", "issue", "create", "-R", REPO, "--title", title, "--body-file", str(body_file)]
    if args.web:
        cmd.append("--web")
    rc = subprocess.run(cmd, check=False).returncode
    body_file.unlink(missing_ok=True)
    return rc
