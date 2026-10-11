# colabo

[![PyPI](https://img.shields.io/pypi/v/colabo)](https://pypi.org/project/colabo/) [![CI](https://github.com/khursanirevo/colabo/actions/workflows/ci.yml/badge.svg)](https://github.com/khursanirevo/colabo/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Edit your project on your laptop with Claude Code (or any editor). Run it on a **Google Colab GPU**. Results come back on their own.

```
┌──────────────── laptop ────────────────┐        ┌────────── Colab VM (T4 / L4 / A100) ──────┐
│ Claude Code ── edits ──> ./my-project  │ ⇄ sync │  /content/my-project                       │
│                       colabo run ──────┼── ssh ─┼─> python train.py  ──writes──> outputs/    │
└────────────────────────────────────────┘        └───────────────────────────────────────────┘
```

`colabo` is a thin workflow on top of two tools you never have to touch directly:

- Google's official [`colab` CLI](https://github.com/googlecolab/google-colab-cli) allocates the VM and gives you SSH into it.
- [Mutagen](https://mutagen.io) keeps your folder and `/content/<project>` on the VM in continuous two-way sync.

Save a file locally, it is on the GPU box a second later. A script writes to `outputs/`, it is back on your laptop a second later. Claude Code just runs `colabo run python train.py`.

## Contents

- [Quickstart](#quickstart)
- [Commands](#commands)
- [Claude Code integration](#claude-code-integration)
- [What goes where](#what-goes-where)
- [Troubleshooting](#troubleshooting)
- [Feedback and logs](#feedback-and-logs)
- [Uninstall](#uninstall)
- [Alternatives](#alternatives)
- [Contributing](#contributing)

## Quickstart

Requirements: macOS or Linux (the `colab` CLI has no Windows support), Python 3.12+, a Google account. A T4 GPU is free; L4/A100/H100, TPUs and high-RAM need Colab Pro or Pro+.

**1. Install the tools** (once per machine)

```bash
uv tool install colabo                  # or: pipx install colabo
brew trust mutagen-io/mutagen && brew install mutagen-io/mutagen/mutagen
#   Linux: https://mutagen.io/documentation/introduction/installation
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519      # Colab rejects RSA keys; skip if you already have one
colab sessions                                  # one-time Google login, follow the printed URL
colabo doctor                                   # confirms everything above
```

**2. Set up a project** (once per project)

```bash
cd my-project
colabo init --gpu T4
```

This writes `colabo.toml` and `mutagen.yml`, adds an SSH host `colab-my-project` to `~/.ssh/config`, appends a section to `CLAUDE.md`, and installs the Claude Code skills and statusline. Rerun it any time to change the GPU; it updates in place.

**3. Work**

```bash
colabo up                        # allocates the VM on first use and starts the sync
colabo run python train.py       # runs in /content/my-project on the GPU; outputs sync back
colabo down                      # stops the sync and releases the VM
```

Always finish with `colabo down`. An idle VM keeps burning compute units, and the statusline shows you when one is up.

## Commands

| Command | What it does |
| --- | --- |
| `colabo init [--gpu T4] [--session NAME] [--high-mem]` | set up the current folder; `--no-claude` skips CLAUDE.md, skills and statusline; `--no-statusline` skips only the statusline |
| `colabo up` | allocate the VM if needed, open the shared SSH connection, start the sync |
| `colabo run <cmd...>` | flush the sync, run `<cmd>` in the remote project dir, flush again |
| `colabo exec file.py` | run a local `.py` or `.ipynb` in the persistent Colab kernel; imports and variables survive between calls |
| `colabo install <pkg...>` | pip-install on the VM (gone after `down`) |
| `colabo status` | VM state, SSH connection, sync state |
| `colabo ssh` | interactive shell in the remote project dir |
| `colabo push` / `colabo pull [dir]` | one-shot rsync instead of continuous sync; `pull` defaults to `outputs/` |
| `colabo down [--keep]` | stop the sync; release the VM unless `--keep` |
| `colabo doctor` | check every prerequisite and print the fix for anything missing |
| `colabo log [-n N] [--path]` | colabo's own log: commands run, exit codes, tracebacks |
| `colabo -v <cmd>` | echo each subprocess and exit code to stderr while a command runs |
| `colabo feedback "title" -m "details"` | file a GitHub issue on colabo with diagnostics attached |
| `colabo skill` | reinstall the Claude Code skills |
| `colabo statusline install` / `uninstall` | add or remove the Colab segment in the Claude Code statusline |

Accelerators for `--gpu`: `T4`, `L4`, `G4`, `A100`, `H100`, `v5e1`, `v6e1`, or `cpu`.

## Claude Code integration

`colabo init` installs three skills into `~/.claude/skills/`:

| Skill | Purpose |
| --- | --- |
| `colabo` | the workflow above: when to `up`, how to `run`, what syncs and what does not, always `down` at the end |
| `colabo-feedback` | how to report a colabo problem: draft the issue, preview it, **ask you**, then file |
| `colab-operator` | Google's own skill for the raw `colab` CLI |

It also appends a managed section to your project's `CLAUDE.md` with the session name, remote path and the rules that matter for that project. After that you can say

> train for one epoch on colab and show me the loss curve

and Claude Code will `colabo up`, `colabo run ...`, read `outputs/`, and `colabo down` if you ask it to.

### Statusline

The statusline segment shows whether a VM is running and what it costs:

```
Fable | 📁my-project | 🔀main | ████░░░░░░ 40% | ⚡colab my-project·T4 ⇅ · 12.3 CU · 1.07/hr
                                                         │               │  │         └ burn rate (only while a VM exists)
                                                         │               │  └ compute-unit balance
                                                         │               └ Mutagen sync running
                                                         └ session · accelerator, or "off"
```

If you already have a statusline command, colabo chains after it: your output first, the Colab segment appended to the first line. `colabo statusline uninstall` restores the previous command exactly. Session state is read from the colab CLI's local state file, so rendering is instant; the balance comes from `colab usage`, refreshed in the background every two minutes.

## What goes where

| Thing | Location | Notes |
| --- | --- | --- |
| project settings | `./colabo.toml` | name, session, accelerator, remote path; `init` writes it, you can edit it |
| sync config | `./mutagen.yml` | ignore list lives here; `data/`, `checkpoints/`, `*.pt`, `*.safetensors`, `*.ckpt`, `.venv`, `node_modules` are skipped by default |
| results | `./outputs/` | write here on the VM and it syncs back |
| SSH host | `~/.ssh/config`, block marked `# >>> colabo:<session> >>>` | `ProxyCommand colab ssh --proxy-mode ...` plus ControlMaster multiplexing |
| colab CLI state and login | `~/.config/colab-cli/` | `sessions.json`, `token.json` (refresh token, survives reboots) |
| colabo log | `~/.local/state/colabo/colabo.log` | rotating, tokens redacted |
| usage cache | `~/.cache/colabo/usage.json` | feeds the statusline |
| Claude Code bits | `~/.claude/skills/colabo*`, `~/.claude/colabo-statusline.sh` | and a `statusLine` entry in `~/.claude/settings.json` |

The VM is ephemeral. Packages and anything outside the synced tree disappear at `colabo down` or when Colab reclaims the runtime. Keep installs in `requirements.txt` and run `colabo run pip install -r requirements.txt` after `up`. Large artifacts are excluded from sync on purpose; move them with `colab download -s <session> REMOTE LOCAL` or mount Drive with `colab drivemount -s <session>`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `HTTP 429` or "another colab ssh is already connected" | Colab allows one SSH connection per runtime; a stale master is holding it | `ssh -O exit colab-<session>` then `colabo up` |
| `HTTP 400` on connect mentioning the key | only ed25519 and ecdsa keys are accepted | `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`, rerun `colabo init` |
| `400` on `colabo up` with a non-T4 GPU | no entitlement for that accelerator | `colabo init --gpu T4`, or upgrade to Pro |
| `401` / `403` from the colab CLI | login expired or wrong scopes | `colab sessions` to log in again; never `colab auth` (that is for GCP credentials inside the VM) |
| "Session not found" after a break | Colab reclaimed the idle VM | `colabo up` creates a fresh one; reinstall packages |
| `colabo up` says `project not running` or `mutagen.yml` errors | Mutagen project file must be named exactly `mutagen.yml` in the project root | rerun `colabo init` |
| files not syncing | Mutagen stopped, or path is in the ignore list | `mutagen sync list <name>` shows problems and conflicts; check `mutagen.yml` |
| statusline shows nothing | wrapper not installed or `~/.claude/settings.json` was edited | `colabo statusline install` |
| statusline says `off` but `colab usage` reports a rate | Google's usage endpoint lags a few minutes after a VM is released | nothing to do; `colab sessions` is the source of truth |
| a command hangs | see what it is waiting on | rerun with `colabo -v <cmd>`, then `colabo log` |

`colabo doctor` catches most of the setup problems before they bite.

## Feedback and logs

Something broke or felt awkward? From any project:

```bash
colabo feedback "up hangs when the T4 pool is empty" -m "ran colabo up at 09:00, waited 5 min, no output"
```

This files a GitHub issue containing your colabo, colab CLI and Mutagen versions, `colabo doctor` output, `colabo.toml`, colabo's log of the recent commands (every subprocess, exit code, duration, traceback) and the error lines from the colab CLI's own log. Tokens are redacted before anything is written to disk. Use `--print` to preview without sending, or `--web` to open the prefilled form in your browser.

With the `colabo-feedback` skill installed, Claude Code drafts this for you whenever colabo itself misbehaves, shows you the title and message, and asks before filing. Nothing is sent without your yes.

To get a useful report: file it right after the failing command so its log section is attached, rerun silent failures once with `-v` and quote the tail, and for sync problems paste the output of `mutagen sync list <name>`.

## Uninstall

```bash
colabo down                                  # release any VM
colabo statusline uninstall                  # restores your previous statusline
mutagen project terminate                    # in each project, if still running
rm -rf ~/.claude/skills/colabo ~/.claude/skills/colabo-feedback ~/.claude/skills/colab-operator
# remove the `# >>> colabo:<session> >>> ... # <<< colabo:<session> <<<` block(s) from ~/.ssh/config
uv tool uninstall colabo                     # or: pipx uninstall colabo
```

Per project, delete `colabo.toml`, `mutagen.yml` and the managed section in `CLAUDE.md`.

## Alternatives

- [colab-persist](https://github.com/ehzawad/colab-persist): same CLI underneath, adds Drive checkpoints and an MCP server.
- [claude-colab](https://github.com/LakshmiSravyaVedantham/claude-colab): Flask plus a Cloudflare tunnel inside a notebook, exposes GPU tools to Claude Code over MCP. No directory sync.
- [ali/claude-colab](https://github.com/ali/claude-colab): runs Claude Code inside the Colab VM.

## Contributing

Issues and pull requests are welcome. [AGENTS.md](AGENTS.md) is the guide for contributors and coding agents: repo layout, conventions, how to test offline and live, how to work the feedback issues, and how to release. Short version:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

MIT.
