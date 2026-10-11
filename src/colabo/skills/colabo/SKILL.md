---
name: colabo
description: Run a local project on a Google Colab GPU with the `colabo` CLI (two-way Mutagen sync + SSH over the official colab CLI). Use when the user wants to train, test or run code on Colab from a local checkout, or asks about colabo up/down/run.
---

# Skill: colabo (local project <-> Colab GPU)

`colabo` keeps the project synced two-way with `/content/<name>` on a Colab VM and runs commands there over SSH. Settings live in `colabo.toml` at the project root; the SSH host is `colab-<session>`.

## Commands (run from the project root)
- `colabo doctor` — check prerequisites (colab CLI + auth, mutagen, ed25519 key, config).
- `colabo init --gpu T4` — one-time project setup (ssh host, `mutagen.yml`, CLAUDE.md section). Options: `--gpu L4|A100|H100|v5e1|cpu`, `--session NAME`, `--high-mem`.
- `colabo up` — allocate the VM if needed, open the shared SSH connection, start sync.
- `colabo run <cmd...>` — flush sync, run `<cmd>` in the remote project dir, flush again. Prefer this for scripts. Example: `colabo run python train.py --epochs 1`.
- `colabo exec file.py` — run a local file in the persistent Colab Jupyter kernel (state survives between calls; no SSH).
- `colabo install pkg ...` — install packages on the VM (ephemeral; lost on `down`).
- `colabo push` / `colabo pull [subdir]` — one-shot rsync when Mutagen is not wanted.
- `colabo status` — VM, ssh master and sync status.
- `colabo down [--keep]` — stop sync (+ release VM unless `--keep`).

## Rules for agents
- Always finish with `colabo down` (or tell the user to) — idle VMs burn compute units.
- Write results into `outputs/` so they sync back. `data/`, `checkpoints/`, `*.pt`, `*.safetensors` are excluded from sync; fetch large files with `colab download -s <session> REMOTE LOCAL`.
- One SSH connection per runtime: if you see HTTP 429, run `ssh -O exit colab-<session>` then `colabo up`.
- `colabo ssh`, `colab repl`, `colab console`, `colab auth`, `colab drivemount` need a TTY — never run them from an agent.
- After `colabo down` the VM is gone: packages and anything outside the synced tree must be reinstalled/recreated on the next `up`.
- If `colabo` is missing: `uv tool install git+https://github.com/khursanirevo/colabo` (or `pipx install`).
