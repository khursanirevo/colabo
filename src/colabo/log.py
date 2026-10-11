"""colabo's own log: what each command ran, every subprocess with exit code and
duration, and tracebacks. Lives in ~/.local/state/colabo/colabo.log (rotating,
tokens redacted at write time) and is attached by `colabo feedback`.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

REDACT = re.compile(r"(Bearer\s+|ya29\.|token=|pypi-|refresh_token\W+)\S+", re.IGNORECASE)
RUN_MARK = "=== colabo"
log = logging.getLogger("colabo")


def log_path() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path(os.environ.get("HOME", Path.home())) / ".local" / "state"))
    d = base / "colabo"
    d.mkdir(parents=True, exist_ok=True)
    return d / "colabo.log"


class _Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = REDACT.sub(r"\1<redacted>", record.getMessage())
        record.args = ()
        return True


def setup(argv: list[str], verbose: bool = False) -> None:
    """Install the file handler (and stderr echo when verbose). Call once per process."""
    if log.handlers:
        return
    log.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(process)d] %(message)s"))
    fh.addFilter(_Redact())
    log.addHandler(fh)
    if verbose:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("[colabo:%(levelname)s] %(message)s"))
        sh.addFilter(_Redact())
        log.addHandler(sh)
    log.info("%s %s (cwd=%s)", RUN_MARK, shlex.join(argv), os.getcwd())


def finish(rc: int) -> None:
    if log.handlers:
        log.info("=== exit %d", rc)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run with the command, exit code, duration and stderr tail logged."""
    check = kw.pop("check", False)
    kw.setdefault("text", True)
    log.debug("run: %s", shlex.join(str(c) for c in cmd))
    t = time.monotonic()
    try:
        r = subprocess.run(cmd, check=check, **kw)
    except FileNotFoundError as e:
        log.error("run failed: %s (%s)", cmd[0], e)
        raise
    ms = int((time.monotonic() - t) * 1000)
    tail = ""
    if r.stderr and r.returncode != 0:
        tail = " stderr: " + " | ".join(str(r.stderr).strip().splitlines()[-3:])
    (log.debug if r.returncode == 0 else log.warning)("rc=%d %dms: %s%s", r.returncode, ms, cmd[0], tail)
    return r


def recent(n: int = 60) -> list[str]:
    """Last `n` lines, starting from the beginning of the previous command's run when possible."""
    try:
        lines = log_path().read_text(errors="replace").splitlines()
    except OSError:
        return []
    marks = [i for i, ln in enumerate(lines) if RUN_MARK in ln]
    # Skip the currently running `colabo feedback` itself; show the run before it (and earlier context).
    if len(marks) >= 2:
        start = marks[-2]
        chunk = lines[start : marks[-1]]
        return chunk[-n:] if len(chunk) > n else (lines[max(0, marks[-1] - n) : marks[-1]])
    return lines[-n:]
