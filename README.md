# colabo

[![PyPI](https://img.shields.io/pypi/v/colabo)](https://pypi.org/project/colabo/) [![CI](https://github.com/khursanirevo/colabo/actions/workflows/ci.yml/badge.svg)](https://github.com/khursanirevo/colabo/actions/workflows/ci.yml)

Work on your project locally with Claude Code (or any editor), run it on a **free Google Colab GPU**.

`colabo` glues together two existing tools so you never think about them:

- Google's official [`colab` CLI](https://github.com/googlecolab/google-colab-cli) allocates the VM and gives you SSH into it.
- [Mutagen](https://mutagen.io) keeps your project folder and `/content/<project>` on the VM in two-way sync, continuously.

Edit a file on your laptop, it is on the GPU box a second later. A script writes to `outputs/`, it is back on your laptop a second later. Claude Code just calls `colabo run python train.py`.

```
┌──────────────── laptop ────────────────┐        ┌────────── Colab VM (T4/L4/A100) ──────────┐
│ Claude Code ── edits ──> ./my-project  │ ⇄ sync │  /content/my-project                       │
│                       colabo run ──────┼── ssh ─┼─> python train.py  ──writes──> outputs/    │
└────────────────────────────────────────┘        └───────────────────────────────────────────┘
```

## Install

macOS or Linux (the `colab` CLI does not support Windows). Python 3.12+.

```bash
uv tool install colabo          # or: pipx install colabo   (latest dev: uv tool install git+https://github.com/khursanirevo/colabo)
brew trust mutagen-io/mutagen && brew install mutagen-io/mutagen/mutagen   # macOS; Linux: https://mutagen.io/documentation/introduction/installation
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519                     # Colab rejects RSA keys
colab sessions                                                  # one-time Google login (follow the prompt)
```

`colabo doctor` tells you what is still missing.

## Use

```bash
cd my-project
colabo init --gpu T4          # writes colabo.toml, mutagen.yml, a CLAUDE.md section, an ssh host `colab-my-project`
colabo up                     # allocates the VM on first use, starts the sync
colabo run python train.py    # runs in /content/my-project on the GPU; outputs sync back
colabo down                   # stop sync, release the VM  (idle VMs burn compute units!)
```

Other commands:

| Command | What it does |
| --- | --- |
| `colabo status` | VM state, ssh connection, sync state |
| `colabo ssh` | interactive shell in the remote project dir |
| `colabo exec file.py` | run a local file in the persistent Colab kernel (state survives between calls) |
| `colabo install torch` | pip-install on the VM |
| `colabo push` / `colabo pull [dir]` | one-shot rsync if you do not want Mutagen running |
| `colabo down --keep` | stop sync but keep the VM |
| `colabo skill` | (re)install the Claude Code skills |
| `colabo statusline install` / `uninstall` | add/remove the Colab segment in the Claude Code statusline |
| `colabo feedback "title" -m "details"` | file a GitHub issue on colabo with versions, `doctor` output and redacted log attached |

GPUs: `T4` (free tier), `L4`, `G4`, `A100`, `H100`, TPUs `v5e1`, `v6e1`, or `cpu`. Anything beyond T4 and `--high-mem` need Colab Pro/Pro+.

## Claude Code

`colabo init` appends a section to your project's `CLAUDE.md` and installs two skills into `~/.claude/skills/`: `colabo` (this workflow) and Google's `colab-operator` (the raw `colab` CLI). After that you can just say:

> train for one epoch on colab and show me the loss curve

and Claude Code will `colabo up`, `colabo run ...`, read `outputs/`, and (if you ask) `colabo down`.

### Statusline

`colabo init` also adds a segment to the Claude Code statusline so you always see whether a VM is running (and burning compute units):

```
Fable | 📁my-project | 🔀main | ████░░░░░░ 40% | ⚡colab my-project·T4 ⇅ · 12.3 CU · 1.07/hr
                                                         │               │  │         └ current burn rate
                                                         │               │  └ compute-unit balance
                                                         │               └ Mutagen sync running
                                                         └ session name · accelerator ("off" when no VM)
```

If you already have a statusline command, colabo chains after it (your output first, the Colab segment appended to the first line). `colabo statusline uninstall` restores the previous command exactly. Session state comes from the colab CLI's local state file (instant); the balance comes from `colab usage`, refreshed in the background every two minutes so the statusline never blocks. Skip it with `colabo init --no-statusline`.

## How it works, and the gotchas it handles

- **SSH host.** `colabo init` adds a `Host colab-<session>` block to `~/.ssh/config` whose `ProxyCommand` is `colab ssh --proxy-mode`. Connecting to that host auto-creates the Colab session with the requested accelerator. VS Code Remote-SSH and JetBrains Gateway work against the same host.
- **One SSH connection per runtime.** Colab returns HTTP 429 on a second connection. Mutagen holds one open, which would block every other `ssh`. The host block enables `ControlMaster`, so Mutagen, rsync and `colabo run` all share a single multiplexed connection. If you ever see 429: `ssh -O exit colab-<session>` then `colabo up`.
- **Only ed25519/ecdsa keys** are accepted by the Colab SSH endpoint.
- **Ephemeral VM.** Everything outside the synced tree (pip packages, `/content/*`) disappears at `colabo down` or when Colab reclaims the runtime. Keep installs in a `requirements.txt` and run `colabo run pip install -r requirements.txt` after `up`.
- **Big files are not synced.** `data/`, `checkpoints/`, `*.pt`, `*.safetensors`, `*.ckpt` are ignored by default (edit `mutagen.yml`). Move them with `colab download -s <session> REMOTE LOCAL`, or mount Drive with `colab drivemount -s <session>`.
- **Session state.** `colabo.toml` holds the project name, session name, accelerator and remote path. The `colab` CLI keeps its own state in `~/.config/colab-cli/`.

## Feedback

Something broke or felt awkward? From any project:

```bash
colabo feedback "up hangs when the T4 pool is empty" -m "ran colabo up at 09:00, waited 5 min, no output"
```

It files a GitHub issue with your colabo/colab/mutagen versions, `colabo doctor` output, `colabo.toml`, and the last error lines from the colab CLI log (tokens redacted). The Claude Code skill tells Claude to do this on its own whenever colabo misbehaves, so dogfooding projects generate issues without you stopping to write them.

## Alternatives

- [colab-persist](https://github.com/ehzawad/colab-persist): same CLI underneath, adds Drive checkpoints and an MCP server.
- [claude-colab](https://github.com/LakshmiSravyaVedantham/claude-colab): Flask + Cloudflare tunnel inside a notebook, exposes GPU tools to Claude Code over MCP. No directory sync.
- [ali/claude-colab](https://github.com/ali/claude-colab): runs Claude Code *inside* the Colab VM.

## Releasing

Bump `version` in `pyproject.toml`, commit, then tag: `git tag v0.1.1 && git push --tags`. The `publish` workflow builds and uploads to PyPI through trusted publishing (no tokens in the repo).

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Tests are offline (they fake the `colab` and `mutagen` binaries). This repo dogfoods itself: `examples/gpu_check.py` is a smoke test you can `colabo run`.

MIT.
