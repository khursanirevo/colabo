# AGENTS.md: working on colabo

This file is for contributors and coding agents (Claude Code, Codex, Cursor and friends) working **on the colabo repo itself**. If you are *using* colabo in another project, you want the `colabo` skill and that project's `CLAUDE.md`, not this file.

## What colabo is

A thin, stdlib-only Python CLI that turns two existing tools into one workflow: Google's official `colab` CLI (VM allocation, SSH over WebSocket) and Mutagen (two-way file sync). Users run `colabo init / up / run / down`; colabo owns the glue, the gotchas, and the Claude Code integration (skills, CLAUDE.md section, statusline).

Design rules, in priority order:

1. **Never surprise the user with cost or exposure.** Anything that allocates a VM, sends data off the machine, or edits files outside the project (`~/.ssh/config`, `~/.claude/settings.json`) must be explicit, idempotent and reversible. Filing an issue is outward-facing: the skill asks first.
2. **Stdlib only, Python 3.12+.** No runtime dependencies besides `google-colab-cli` (which itself needs 3.12). Shell out to `colab`, `mutagen`, `ssh`, `rsync`, `gh`; do not reimplement them.
3. **Every edit to a user file is a marked, replaceable block.** SSH config uses `# >>> colabo:<session> >>> ... <<<`, CLAUDE.md uses `<!-- >>> colabo >>> ... <<< -->`, the statusline wrapper stores `PREV=`. Re-running `init` must converge, and `uninstall` must restore.
4. **Everything a user might need in a bug report is logged, nothing secret is.** All subprocesses go through `clog.sh`; the redaction filter runs before bytes hit disk.
5. **The statusline must never block.** No network in `colabo statusline` itself; usage comes from a cache refreshed by a detached process.

## Repo layout

| Path | Responsibility |
| --- | --- |
| `src/colabo/cli.py` | argparse CLI, `Project` config (`colabo.toml`), templates for `mutagen.yml`, SSH block, CLAUDE.md section, all `cmd_*` handlers, `doctor`, skill install |
| `src/colabo/log.py` | colabo's own rotating log (`~/.local/state/colabo/colabo.log`), `sh()` subprocess wrapper, redaction, `recent()` for feedback |
| `src/colabo/statusline.py` | Claude Code statusline segment, usage cache + background refresh, settings.json install/uninstall with the chaining wrapper script |
| `src/colabo/feedback.py` | `colabo feedback`: builds the diagnostics report and files it with `gh` |
| `src/colabo/skills/colabo/SKILL.md` | the user-facing workflow skill installed to `~/.claude/skills/colabo/` |
| `src/colabo/skills/colabo-feedback/SKILL.md` | the ask-first issue-filing skill |
| `tests/test_cli.py` | offline tests; see below |
| `examples/gpu_check.py` | smoke test to run on a real VM |
| `.github/workflows/ci.yml` | ruff + pytest on ubuntu/macos, py3.12/3.13 |
| `.github/workflows/publish.yml` | PyPI via trusted publishing on `v*` tags |
| `colabo.toml`, `mutagen.yml`, `CLAUDE.md` | this repo dogfoods itself (session `work`, T4) |

Skills ship inside the wheel as package data (`importlib.resources`); if you add one, add its name to `BUNDLED_SKILLS` in `cli.py` and it is installed by `init`, `skill`, and checked by `doctor`.

## Dev loop

```bash
uv sync --extra dev
uv run pytest -q                       # ~3 s, fully offline
uv run ruff check . --fix && uv run ruff format .
uv tool install --force --editable .   # make your working copy the `colabo` on PATH
```

Keep the editable install while iterating: any project on this machine that uses colabo picks up your change the moment you save.

### Tests

`tests/test_cli.py` uses an `env` fixture that gives each test a throwaway `HOME` (with an ed25519 key and a sample `~/.ssh/config`), a fake `colab`/`mutagen`/`curl` on `PATH` that exit 0, and an empty project directory as cwd. Tests exercise the CLI through `cli.main([...])` and assert on the files it writes. Patterns to follow:

- Assert idempotency for anything that edits user files (run twice, count markers).
- Assert redaction for anything that writes logs or reports.
- Prefer testing the generated text (SSH block, `mutagen.yml`, wrapper script) over mocking internals.
- Set `XDG_STATE_HOME` / `XDG_CACHE_HOME` in tests that touch the log or usage cache, and remove `clog.log` handlers afterwards, because logging is process-global.

There are no live tests in CI. Do a live check yourself when you touch `up`, `run`, `down`, the SSH block or Mutagen config (see below).

### Live check (costs a few compute units on paid tiers, free on T4)

```bash
colabo up                                    # from this repo; allocates session "work" on a T4
colabo run python examples/gpu_check.py      # prints GPU, torch, timing; writes outputs/gpu_check.txt
cat outputs/gpu_check.txt                    # proves the sync came back
colabo down                                  # ALWAYS
```

Leaving a VM up is the one mistake that costs real money. If you are an agent, run `colabo down` before you report completion, and say so.

## Gotchas you will hit

- **One SSH connection per runtime.** Colab returns HTTP 429 on a second connection. Mutagen keeps one open, so the SSH host block uses `ControlMaster auto` / `ControlPath ~/.ssh/cm-colabo-%C` and everything multiplexes through it. If you see 429 during development: `ssh -O exit root@colab-<session>`.
- **Only ed25519/ecdsa keys.** The Colab SSH endpoint rejects `ssh-rsa`. `ssh_key()` scans for `id_ed25519`, then `id_ecdsa`.
- **`mutagen.yml`, no leading dot.** `mutagen project start` looks for exactly that name in cwd. The lock file `mutagen.yml.lock` exists while the project is running and is what the statusline uses to show `⇅`.
- **`colab auth` is not CLI login.** It injects GCP credentials into the VM. CLI login is `colab sessions` (OAuth) or `gcloud auth application-default login` with the four scopes listed in Google's skill. Never tell users to run `colab auth` for a 401.
- **`colab usage` lags.** It keeps reporting the previous burn rate and an active assignment for several minutes after a VM is released, so the statusline hides the rate when local state says `off`.
- **`colab usage` is slow (~3 s)** and `colab status` hits the network too. Nothing in the statusline render path may call them.
- **The colab CLI debug log contains Authorization headers.** `feedback` includes only ERROR/WARNING lines from it, through the same `REDACT` regex. Extend the regex if you see a new token shape.
- **Python floor is 3.12** because `google-colab-cli` requires it. CI runs 3.12 and 3.13.
- **`--gpu` with an unknown value silently falls back to A100 in the colab CLI**, which then fails on free accounts. `Project.accel_flags()` validates against `GPUS`/`TPUS` first.

## Working the feedback issues

Users and their agents file issues with `colabo feedback`, so each one carries versions, `doctor` output, `colabo.toml`, the colabo log of the failing command, and redacted colab CLI errors.

```bash
gh issue list -R khursanirevo/colabo
gh issue view <n> -R khursanirevo/colabo
```

1. Reproduce from the attached log first; the `run:` / `rc=` lines tell you exactly which subprocess failed and with what stderr.
2. Write the failing test, then the fix. Keep the fix in the layer that owns the behaviour (template vs. command vs. log).
3. Reference the issue in the commit message (`Fixes #n`), push, and the issue closes on merge.
4. If the root cause is in the `colab` CLI or Mutagen, say so in the issue, add a workaround or a clearer error in colabo, and link upstream.

## Commits, CI, release

- Conventional, descriptive commit subjects; body explains the why. Co-authored agents add a `Co-Authored-By:` trailer.
- If GitHub rejects a push for email privacy, commit with your `<id>+<login>@users.noreply.github.com` address.
- CI must be green (ruff, format, pytest on both OSes) before tagging.
- Release: bump `version` in `pyproject.toml`, commit, `git tag vX.Y.Z && git push --tags`. The `publish` workflow builds, runs `twine check`, and uploads to PyPI through trusted publishing (environment `pypi`, no tokens stored). The PyPI project is owned by `khursani8`.

## Things deliberately not done (yet)

- Windows: blocked on the `colab` CLI.
- Multiple sessions per project: `colabo.toml` holds one; use separate checkouts or edit the file.
- An MCP server: the `colabo` CLI plus skills has been enough for Claude Code; `colab-persist` has one if you need it.
- Syncing large artifacts: by design excluded; `colab download` or Drive.
