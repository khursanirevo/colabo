---
name: colabo-feedback
description: Report a bug, confusing behaviour or feature idea about the `colabo` CLI itself as a GitHub issue, with diagnostics attached. Use when colabo (not the user's code, not Colab's service) errors, hangs, surprises you, or lacks something. Always ask the user before filing.
---

# Skill: colabo feedback (ask first, then file)

`colabo feedback "<title>" -m "<details>"` opens a GitHub issue on `khursanirevo/colabo` with the environment, `colabo doctor` output, `colabo.toml`, colabo's own log of the recent commands and the Colab CLI's error lines (tokens redacted). Filing an issue is a public, outward-facing action, so **never run it without the user's explicit yes**.

## When this applies
- A `colabo` command failed, hung, printed a confusing message, or did something you did not expect.
- You had to work around colabo, or wished it had an option/command.
- NOT for: errors in the user's own scripts, Colab service limits (no GPU available, quota, 429 from Google), missing prerequisites the user has not installed yet (`colabo doctor` tells them what to install).

## Procedure
1. If the failure was silent or unclear, rerun the command once with `colabo -v <cmd>` and read `colabo log -n 40`.
2. Draft the issue:
   - title: one line, the symptom, e.g. `up: hangs when the T4 pool is empty`
   - message: the exact command, what happened (quote the last relevant log lines), what you expected, and any workaround you used.
3. Preview it for the user with `colabo feedback --print "<title>" -m "<message>"` (nothing is sent) and show them the title + message part.
4. **Ask the user**: "File this as an issue on khursanirevo/colabo?" Offer: file it, edit it first, or skip. Do not file if they do not answer.
5. Only after a yes: run `colabo feedback "<title>" -m "<message>"` and tell them the issue URL. If `gh` is not installed or not logged in, give them the printed prefilled URL instead.
6. Continue the original task with a workaround; do not block on the issue.

## Notes
- One issue per distinct problem; if the same thing happens again in the session, mention it to the user instead of filing twice.
- Keep secrets out of `-m`. The report redacts known token formats, but the free-text message is yours.
