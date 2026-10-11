# colabo

Contributor and agent guide: @AGENTS.md

This repo also dogfoods colabo (session `work`, T4). The managed section below is what `colabo init` writes into any project; it is kept here on purpose so changes to the template are visible in the diff.

<!-- >>> colabo >>> (managed by `colabo init`) -->
## Running on Google Colab (colabo)

This folder is synced two-way with `/content/colabo` on a Colab VM (T4) by Mutagen.
Edit locally; execute remotely. Commands (run from the project root):

- `colabo up` starts the VM if needed and the sync. `colabo down` stops both (do this when done; idle VMs burn compute units).
- `colabo run <cmd...>` flushes the sync, runs `<cmd>` in `/content/colabo` over SSH, then flushes again. Example: `colabo run python train.py --epochs 1`.
- `colabo exec file.py` runs a local Python file in the persistent Colab kernel (no SSH, state survives between calls).
- `colabo install pkg ...` installs packages on the VM. They vanish when the session stops.
- `colabo status` / `colabo doctor` for health checks. `colabo ssh` for an interactive shell (needs a TTY; do not run from Claude).
- Write results to `outputs/` so they sync back. `data/`, `checkpoints/`, `*.pt`, `*.safetensors` are NOT synced; move them with `colab download -s work REMOTE LOCAL` or Google Drive.
- Colab allows one SSH connection per runtime. The `colab-work` host multiplexes it; on HTTP 429 run `ssh -O exit colab-work` then `colabo up`.
- Never run `colab repl`, `colab console`, `colab auth`, or `colab drivemount` from Claude (they need a TTY).
<!-- <<< colabo <<< -->
