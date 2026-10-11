"""colabo: work locally with Claude Code (or any editor), run on a Google Colab GPU.

Wraps the official ``colab`` CLI (SSH over WebSocket) and Mutagen (two-way file
sync) into one project-level workflow:

    colabo init --gpu T4      # once per project: ssh host, mutagen.yml, CLAUDE.md
    colabo up                 # start the VM (if needed) + start syncing
    colabo run python train.py
    colabo down               # stop syncing + release the VM

Stdlib only. Requires Python 3.12+ (the colab CLI needs it).
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from importlib import resources
from pathlib import Path

from . import log as clog

__version__ = "0.1.0"

CONFIG_FILE = "colabo.toml"
MUTAGEN_FILE = "mutagen.yml"
SKILL_URL = "https://raw.githubusercontent.com/googlecolab/google-colab-cli/main/skills/colab-operator/SKILL.md"
GPUS = ("T4", "L4", "G4", "A100", "H100")
TPUS = ("v5e1", "v6e1")


# --------------------------------------------------------------------------- utils
class ColaboError(Exception):
    """User-facing error; printed without a traceback."""


def info(msg: str) -> None:
    print(f"[colabo] {msg}", file=sys.stderr)


def home() -> Path:
    return Path(os.environ.get("HOME", Path.home()))


def ssh_config_path() -> Path:
    return home() / ".ssh" / "config"


def run(cmd: list[str], *, check: bool = True, capture: bool = False, **kw) -> subprocess.CompletedProcess:
    return clog.sh(cmd, check=check, text=True, capture_output=capture, **kw)


def quiet_ok(cmd: list[str]) -> bool:
    """Run a command and return True if it exits 0 (output discarded)."""
    try:
        return clog.sh(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = "p-" + s
    return s


def colab_bin() -> str:
    found = shutil.which("colab") or str(home() / ".local" / "bin" / "colab")
    if not Path(found).exists():
        raise ColaboError(
            "`colab` CLI not found. Install it with:\n"
            "    uv tool install google-colab-cli    # or: pip install google-colab-cli"
        )
    return str(Path(found).resolve())


def ssh_key() -> Path:
    """Return the ed25519/ecdsa private key path. Colab rejects ssh-rsa keys."""
    for name in ("id_ed25519", "id_ecdsa"):
        p = home() / ".ssh" / name
        if p.exists():
            return p
    raise ColaboError(
        "No ed25519/ecdsa SSH key found (Colab rejects ssh-rsa). Create one with:\n"
        "    ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519"
    )


# --------------------------------------------------------------------------- config
class Project:
    """Per-project settings, stored in colabo.toml next to mutagen.yml."""

    def __init__(self, root: Path, data: dict):
        self.root = root
        self.name: str = data["name"]
        self.session: str = data["session"]
        self.accelerator: str = data.get("accelerator", "T4")
        self.remote: str = data.get("remote", f"/content/{self.name}")
        self.high_mem: bool = bool(data.get("high_mem", False))

    @property
    def host(self) -> str:
        return f"colab-{self.session}"

    @property
    def ssh_target(self) -> str:
        return f"root@{self.host}"

    @classmethod
    def load(cls, start: Path | None = None) -> Project:
        cur = (start or Path.cwd()).resolve()
        for d in (cur, *cur.parents):
            cfg = d / CONFIG_FILE
            if cfg.exists():
                return cls(d, tomllib.loads(cfg.read_text()))
        raise ColaboError(f"No {CONFIG_FILE} found here or in any parent. Run `colabo init` first.")

    def accel_flags(self) -> list[str]:
        acc = self.accelerator
        flags: list[str] = []
        if acc.upper() in GPUS:
            flags += ["--gpu", acc.upper()]
        elif acc.lower() in TPUS:
            flags += ["--tpu", acc.lower()]
        elif acc.lower() not in ("cpu", "none", ""):
            raise ColaboError(f"Unknown accelerator {acc!r}. GPUs: {GPUS}, TPUs: {TPUS}, or 'cpu'.")
        if self.high_mem:
            flags.append("--high-mem")
        return flags


def write_config(root: Path, name: str, session: str, accelerator: str, remote: str, high_mem: bool) -> None:
    (root / CONFIG_FILE).write_text(
        textwrap.dedent(
            f"""\
            # colabo project settings (https://github.com/khursanirevo/colabo)
            name = "{name}"            # mutagen sync name
            session = "{session}"      # colab CLI session name; ssh host becomes colab-{session}
            accelerator = "{accelerator}"   # T4 | L4 | G4 | A100 | H100 | v5e1 | v6e1 | cpu
            remote = "{remote}"
            high_mem = {str(high_mem).lower()}
            """
        )
    )


# --------------------------------------------------------------------------- templates
def ssh_block(p: Project, colab: str) -> str:
    flags = " ".join(p.accel_flags())
    return textwrap.dedent(
        f"""\
        # >>> colabo:{p.session} >>>  (managed by `colabo init`; do not edit by hand)
        Host {p.host}
          ProxyCommand {colab} ssh --proxy-mode -s {p.session} {flags}
          User root
          IdentityFile {ssh_key()}
          IdentitiesOnly yes
          StrictHostKeyChecking no
          UserKnownHostsFile /dev/null
          ServerAliveInterval 30
          ServerAliveCountMax 6
          # Colab allows ONE ssh connection per runtime (HTTP 429 otherwise).
          # Multiplex so Mutagen, rsync and `ssh` share a single master connection.
          ControlMaster auto
          ControlPath ~/.ssh/cm-colabo-%C
          ControlPersist 4h
        # <<< colabo:{p.session} <<<
        """
    )


def upsert_ssh_block(p: Project, colab: str) -> None:
    path = ssh_config_path()
    path.parent.mkdir(mode=0o700, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    block = ssh_block(p, colab)
    pat = re.compile(rf"# >>> colabo:{re.escape(p.session)} >>>.*?# <<< colabo:{re.escape(p.session)} <<<\n", re.DOTALL)
    if pat.search(text):
        text = pat.sub(block, text)
    else:
        # Insert before a global `Host *` block if present, so it takes precedence.
        m = re.search(r"^Host \*\s*$", text, re.MULTILINE)
        if m:
            text = text[: m.start()] + block + "\n" + text[m.start() :]
        else:
            text = text.rstrip("\n") + ("\n\n" if text else "") + block
    path.write_text(text)
    path.chmod(0o600)


def mutagen_yml(p: Project) -> str:
    return textwrap.dedent(
        f"""\
        # Mutagen project (managed by `colabo init`). Two-way sync with the Colab VM.
        #   colabo up | colabo down | mutagen sync list {p.name}
        sync:
          defaults:
            mode: two-way-resolved
            ignore:
              vcs: true
              paths:
                - "__pycache__"
                - "*.pyc"
                - ".venv"
                - "node_modules"
                - "mutagen.yml.lock"
                - "data/**"
                - "checkpoints/**"
                - "*.pt"
                - "*.safetensors"
                - "*.ckpt"
                - "wandb"
                - ".DS_Store"
          {p.name}:
            alpha: "."
            beta: "{p.host}:{p.remote}"
        """
    )


def claude_md_section(p: Project) -> str:
    return textwrap.dedent(
        f"""\
        <!-- >>> colabo >>> (managed by `colabo init`) -->
        ## Running on Google Colab (colabo)

        This folder is synced two-way with `{p.remote}` on a Colab VM ({p.accelerator}) by Mutagen.
        Edit locally; execute remotely. Commands (run from the project root):

        - `colabo up` starts the VM if needed and the sync. `colabo down` stops both (do this when done; idle VMs burn compute units).
        - `colabo run <cmd...>` flushes the sync, runs `<cmd>` in `{p.remote}` over SSH, then flushes again. Example: `colabo run python train.py --epochs 1`.
        - `colabo exec file.py` runs a local Python file in the persistent Colab kernel (no SSH, state survives between calls).
        - `colabo install pkg ...` installs packages on the VM. They vanish when the session stops.
        - `colabo status` / `colabo doctor` for health checks. `colabo ssh` for an interactive shell (needs a TTY; do not run from Claude).
        - Write results to `outputs/` so they sync back. `data/`, `checkpoints/`, `*.pt`, `*.safetensors` are NOT synced; move them with `colab download -s {p.session} REMOTE LOCAL` or Google Drive.
        - Colab allows one SSH connection per runtime. The `{p.host}` host multiplexes it; on HTTP 429 run `ssh -O exit {p.host}` then `colabo up`.
        - Never run `colab repl`, `colab console`, `colab auth`, or `colab drivemount` from Claude (they need a TTY).
        <!-- <<< colabo <<< -->
        """  # noqa: E501
    )


def upsert_claude_md(p: Project) -> None:
    path = p.root / "CLAUDE.md"
    text = path.read_text() if path.exists() else f"# {p.name}\n\n"
    section = claude_md_section(p)
    pat = re.compile(r"<!-- >>> colabo >>>.*?<!-- <<< colabo <<< -->\n", re.DOTALL)
    text = pat.sub(section, text) if pat.search(text) else text.rstrip("\n") + "\n\n" + section
    path.write_text(text)


def ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    want = ["mutagen.yml.lock", "outputs/", "checkpoints/", "data/"]
    missing = [w for w in want if w not in lines]
    if missing:
        gi.write_text("\n".join([*lines, "# colabo", *missing]) + "\n")


# --------------------------------------------------------------------------- ssh helpers
def master_alive(p: Project) -> bool:
    return quiet_ok(["ssh", "-O", "check", p.ssh_target])


def ensure_master(p: Project) -> None:
    """Open the shared ControlMaster connection (auto-creates the Colab session)."""
    if master_alive(p):
        return
    info(f"connecting to Colab session '{p.session}' ({p.accelerator}); first connect allocates the VM...")
    r = clog.sh(["ssh", "-fN", p.ssh_target], check=False, text=True, stderr=subprocess.PIPE)
    if r.returncode != 0 or not master_alive(p):
        raise ColaboError("could not open SSH connection to Colab:\n" + r.stderr.strip())


def ssh_run(p: Project, remote_cmd: str, *, tty: bool = False, check: bool = True) -> int:
    cmd = ["ssh"] + (["-t"] if tty else []) + [p.ssh_target, remote_cmd]
    return clog.sh(cmd, check=check).returncode


def mutagen_running(p: Project) -> bool:
    r = clog.sh(["mutagen", "sync", "list", p.name], check=False, capture_output=True, text=True)
    return r.returncode == 0 and "Status:" in r.stdout


def flush(p: Project) -> None:
    if mutagen_running(p):
        quiet_ok(["mutagen", "sync", "flush", p.name])


# --------------------------------------------------------------------------- commands
def cmd_init(a: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    name = slug(a.name or root.name)
    session = slug(a.session or name)
    remote = a.remote or f"/content/{name}"
    write_config(root, name, session, a.gpu, remote, a.high_mem)
    p = Project.load(root)

    if not shutil.which("mutagen"):
        info("mutagen not found. Install: https://mutagen.io/documentation/introduction/installation")
        if platform.system() == "Darwin" and shutil.which("brew"):
            info("  brew trust mutagen-io/mutagen && brew install mutagen-io/mutagen/mutagen")
    colab = colab_bin()
    upsert_ssh_block(p, colab)
    (root / MUTAGEN_FILE).write_text(mutagen_yml(p))
    (root / "outputs").mkdir(exist_ok=True)
    ensure_gitignore(root)
    if not a.no_claude:
        upsert_claude_md(p)
        install_skills()
        if not a.no_statusline:
            from . import statusline

            statusline.install()

    info(f"wrote {CONFIG_FILE}, {MUTAGEN_FILE}, CLAUDE.md; ssh host '{p.host}' added to {ssh_config_path()}")
    if not quiet_ok([colab, "sessions"]):
        info("colab CLI is not authenticated yet. Run `colab sessions` once and follow the browser prompt.")
    info("next: `colabo up`")
    return 0


def cmd_up(a: argparse.Namespace) -> int:
    p = Project.load()
    if not shutil.which("mutagen"):
        raise ColaboError("mutagen is required for `colabo up` (see `colabo doctor`).")
    ensure_master(p)
    ssh_run(p, f"mkdir -p {shlex.quote(p.remote)}")
    if mutagen_running(p):
        info("sync already running")
    else:
        run(["mutagen", "project", "start"], cwd=p.root, stdout=subprocess.DEVNULL)
    flush(p)
    info(f"syncing {p.root} <-> {p.host}:{p.remote}")
    return cmd_status(a)


def cmd_down(a: argparse.Namespace) -> int:
    p = Project.load()
    if mutagen_running(p):
        clog.sh(
            ["mutagen", "project", "terminate"],
            check=False,
            cwd=p.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    quiet_ok(["ssh", "-O", "exit", p.ssh_target])
    if a.keep:
        info("VM kept running (--keep). Stop it later with: colabo down")
        return 0
    return clog.sh([colab_bin(), "stop", "-s", p.session], check=False).returncode


def cmd_status(a: argparse.Namespace) -> int:
    p = Project.load()
    clog.sh([colab_bin(), "status", "-s", p.session], check=False)
    print(f"ssh master ({p.host}): {'up' if master_alive(p) else 'down'}")
    r = clog.sh(["mutagen", "sync", "list", p.name], check=False, capture_output=True, text=True)
    if r.returncode == 0:
        status = next((ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("Status:")), "?")
        print(f"mutagen ({p.name}): {status}")
    else:
        print(f"mutagen ({p.name}): not running")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    p = Project.load()
    if not a.cmd:
        raise ColaboError("usage: colabo run <command...>")
    ensure_master(p)
    flush(p)
    remote = f"cd {shlex.quote(p.remote)} && {' '.join(shlex.quote(c) for c in a.cmd)}"
    rc = ssh_run(p, remote, tty=sys.stdin.isatty() and sys.stdout.isatty(), check=False)
    flush(p)
    return rc


def cmd_ssh(a: argparse.Namespace) -> int:
    p = Project.load()
    ensure_master(p)
    return ssh_run(p, f"cd {shlex.quote(p.remote)} 2>/dev/null || cd /content; exec bash -l", tty=True, check=False)


def cmd_exec(a: argparse.Namespace) -> int:
    p = Project.load()
    flush(p)
    return clog.sh([colab_bin(), "exec", "-s", p.session, "-f", a.file, *a.extra], check=False).returncode


def cmd_install(a: argparse.Namespace) -> int:
    p = Project.load()
    return clog.sh([colab_bin(), "install", "-s", p.session, *a.pkgs], check=False).returncode


def _rsync_excludes(p: Project) -> list[str]:
    ex = [
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        "data/**",
        "checkpoints/**",
        MUTAGEN_FILE + ".lock",
        ".DS_Store",
    ]
    return [f"--exclude={e}" for e in ex]


def cmd_push(a: argparse.Namespace) -> int:
    p = Project.load()
    ensure_master(p)
    ssh_run(p, f"mkdir -p {shlex.quote(p.remote)}")
    return clog.sh(
        ["rsync", "-az", "--info=progress2", *_rsync_excludes(p), f"{p.root}/", f"{p.ssh_target}:{p.remote}/"],
        check=False,
    ).returncode


def cmd_pull(a: argparse.Namespace) -> int:
    p = Project.load()
    ensure_master(p)
    sub = a.path.strip("/")
    (p.root / sub).mkdir(parents=True, exist_ok=True)
    return clog.sh(
        ["rsync", "-az", "--info=progress2", f"{p.ssh_target}:{p.remote}/{sub}/", f"{p.root / sub}/"], check=False
    ).returncode


def cmd_doctor(a: argparse.Namespace) -> int:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {label}" + ("" if good else f"\n       {hint}"))

    print("colabo doctor")
    check("platform is macOS/Linux", platform.system() in ("Darwin", "Linux"), "the colab CLI does not support Windows")
    try:
        colab = colab_bin()
        check(f"colab CLI ({colab})", True)
        check(
            "colab CLI authenticated",
            quiet_ok([colab, "sessions"]),
            "run `colab sessions` and complete the browser login",
        )
    except ColaboError as e:
        check("colab CLI installed", False, str(e).splitlines()[-1].strip())
    check(
        "mutagen installed",
        bool(shutil.which("mutagen")),
        "brew trust mutagen-io/mutagen && brew install mutagen-io/mutagen/mutagen",
    )
    check("rsync installed", bool(shutil.which("rsync")), "install rsync")
    try:
        check(f"ed25519/ecdsa ssh key ({ssh_key()})", True)
    except ColaboError as e:
        check("ed25519/ecdsa ssh key", False, str(e).splitlines()[-1].strip())
    try:
        p = Project.load()
        check(f"project config ({p.root / CONFIG_FILE})", True)
        cfg = ssh_config_path()
        check(
            f"ssh host {p.host} in {cfg}",
            cfg.exists() and f"Host {p.host}" in cfg.read_text(),
            "run `colabo init` again",
        )
        check("mutagen.yml present", (p.root / MUTAGEN_FILE).exists(), "run `colabo init` again")
        print(f"  [--] ssh master: {'up' if master_alive(p) else 'down'}  (fine either way; `colabo up` opens it)")
    except ColaboError:
        print("  [--] not inside a colabo project (run `colabo init` in your project)")
    skill = home() / ".claude" / "skills" / "colabo" / "SKILL.md"
    print(
        f"  [{'ok' if skill.exists() else '--'}] Claude Code skill at {skill}"
        + ("" if skill.exists() else "  (run `colabo skill`)")
    )
    print(f"  [--] colabo log: {clog.log_path()}  (`colabo log`, or `colabo -v <cmd>` to watch live)")
    sl = home() / ".claude" / "colabo-statusline.sh"
    print(
        f"  [{'ok' if sl.exists() else '--'}] Claude Code statusline at {sl}"
        + ("" if sl.exists() else "  (run `colabo statusline install`)")
    )
    return 0 if ok else 1


def install_skills() -> None:
    base = home() / ".claude" / "skills"
    dst = base / "colabo" / "SKILL.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(resources.files("colabo").joinpath("skills/colabo/SKILL.md").read_text())
    info(f"installed Claude Code skill -> {dst}")
    op = base / "colab-operator" / "SKILL.md"
    if not op.exists() and shutil.which("curl"):
        op.parent.mkdir(parents=True, exist_ok=True)
        if quiet_ok(["curl", "-fsSL", "-o", str(op), SKILL_URL]):
            info(f"installed Google's colab-operator skill -> {op}")


def cmd_skill(a: argparse.Namespace) -> int:
    install_skills()
    return 0


def cmd_statusline(a: argparse.Namespace) -> int:
    from . import statusline

    return statusline.main(a)


def cmd_log(a: argparse.Namespace) -> int:
    path = clog.log_path()
    if a.path:
        print(path)
        return 0
    print(f"# {path}")
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    print("\n".join(lines[-a.n :]) if lines else "(empty)")
    return 0


def cmd_feedback(a: argparse.Namespace) -> int:
    from . import feedback

    return feedback.main(a)


# --------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="colabo", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"colabo {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="echo every subprocess and its exit code to stderr")
    sp = ap.add_subparsers(dest="command", required=True)

    s = sp.add_parser("init", help="set up this project for Colab (ssh host, mutagen.yml, CLAUDE.md, skill)")
    s.add_argument("--gpu", default="T4", help="T4 | L4 | G4 | A100 | H100 | v5e1 | v6e1 | cpu (default T4)")
    s.add_argument("--session", help="colab session name (default: project dir name)")
    s.add_argument("--name", help="mutagen sync name (default: project dir name)")
    s.add_argument("--remote", help="remote directory (default: /content/<name>)")
    s.add_argument("--high-mem", action="store_true", help="request high-RAM (Colab Pro)")
    s.add_argument("--no-claude", action="store_true", help="skip CLAUDE.md section, skill and statusline install")
    s.add_argument("--no-statusline", action="store_true", help="skip the Claude Code statusline install")
    s.set_defaults(fn=cmd_init)

    s = sp.add_parser("up", help="start the Colab VM (if needed) and the two-way sync")
    s.set_defaults(fn=cmd_up)
    s = sp.add_parser("down", help="stop the sync and release the VM")
    s.add_argument("--keep", action="store_true", help="stop sync but keep the VM running")
    s.set_defaults(fn=cmd_down)
    s = sp.add_parser("status", help="VM, ssh and sync status")
    s.set_defaults(fn=cmd_status)
    s = sp.add_parser("run", help="run a shell command in the remote project dir (flushes sync before/after)")
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_run)
    s = sp.add_parser("ssh", help="interactive shell in the remote project dir")
    s.set_defaults(fn=cmd_ssh)
    s = sp.add_parser("exec", help="run a local .py/.ipynb in the persistent Colab kernel (via `colab exec`)")
    s.add_argument("file")
    s.add_argument("extra", nargs=argparse.REMAINDER, help="extra args passed to `colab exec`")
    s.set_defaults(fn=cmd_exec)
    s = sp.add_parser("install", help="pip-install packages on the VM")
    s.add_argument("pkgs", nargs="+")
    s.set_defaults(fn=cmd_install)
    s = sp.add_parser("push", help="one-shot rsync local -> remote (no Mutagen needed)")
    s.set_defaults(fn=cmd_push)
    s = sp.add_parser("pull", help="one-shot rsync remote subdir -> local (default: outputs)")
    s.add_argument("path", nargs="?", default="outputs")
    s.set_defaults(fn=cmd_pull)
    s = sp.add_parser("doctor", help="check prerequisites and project setup")
    s.set_defaults(fn=cmd_doctor)
    s = sp.add_parser("skill", help="(re)install the Claude Code skills")
    s.set_defaults(fn=cmd_skill)
    s = sp.add_parser("statusline", help="Claude Code statusline segment: session, sync, compute units")
    s.add_argument(
        "action", nargs="?", choices=["install", "uninstall"], help="(un)install into ~/.claude/settings.json"
    )
    s.add_argument("--refresh", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_statusline)
    s = sp.add_parser("log", help="show colabo's own log (commands run, exit codes, errors)")
    s.add_argument("-n", type=int, default=40, help="lines to show (default 40)")
    s.add_argument("--path", action="store_true", help="print only the log file path")
    s.set_defaults(fn=cmd_log)
    s = sp.add_parser("feedback", help="file a GitHub issue against colabo with diagnostics attached")
    s.add_argument("title", nargs="*", help="one-line issue title")
    s.add_argument("-m", "--message", help="what happened and what you expected")
    s.add_argument("--web", action="store_true", help="open the prefilled issue form in the browser")
    s.add_argument("--print", action="store_true", help="print the report instead of filing it")
    s.set_defaults(fn=cmd_feedback)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    if args.command != "statusline":  # rendered every few seconds; would drown the log
        clog.setup(argv, verbose=args.verbose)
    rc = 1
    try:
        rc = args.fn(args)
    except ColaboError as e:
        clog.log.error("%s", e)
        print(f"error: {e}", file=sys.stderr)
        rc = 2
    except subprocess.CalledProcessError as e:
        clog.log.error("command failed rc=%s: %s", e.returncode, " ".join(map(str, e.cmd)))
        print(f"error: command failed ({e.returncode}): {' '.join(map(str, e.cmd))}", file=sys.stderr)
        rc = e.returncode or 1
    except KeyboardInterrupt:
        clog.log.warning("interrupted")
        rc = 130
    except Exception:
        clog.log.exception("unhandled error")
        print(f"error: unexpected failure; see `colabo log` ({clog.log_path()})", file=sys.stderr)
        raise
    finally:
        clog.finish(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
