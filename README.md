# ghostty-session-resume

Snapshot every [Claude Code](https://claude.com/claude-code) session running
across your [Ghostty](https://ghostty.org) tabs and restore them after a
restart — **same windows, same tab order**, each tab resumed to its exact
conversation in its original directory.

Ghostty has no built-in session save/restore, and there's no obvious link
between a terminal tab and the Claude session inside it. These two scripts
reconstruct that link from data Claude already writes to disk, so you can
quit/reboot and pick up exactly where you left off.

> macOS only (uses the Accessibility API and Ghostty).

## Usage

```sh
# before a restart
./save-ghostty-session.py

# after the restart
./resume-ghostty-session.py          # dry-run: print the plan
./resume-ghostty-session.py --go     # actually rebuild it
```

The snapshot is written to `~/.ghostty-session/session.json`.

## Why your sessions are never lost

Claude Code continuously autosaves every session to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Closing terminals — even
a forced restart — does not delete them (kept ~30 days). You can always resume
one by hand with `claude --resume <session-id>` from its directory. These
scripts just automate finding and reopening them all, in place.

## How it works (no setup, no config)

The scripts join three things Claude/Ghostty already maintain:

1. **`~/.claude/sessions/<pid>.json`** — for each live `claude` process:
   `{pid, sessionId, cwd, status}`. The exact live session + directory per tab.
2. **Transcript `ai-title` lines** — `{"type":"ai-title","aiTitle":..,"sessionId":..}`
   record the tab title Claude displays, keyed by session id. This lets a
   Ghostty tab (Accessibility exposes only its *title*) be matched to a session.
3. **Accessibility** (System Events): window + tab order and titles; the
   window's `AXDocument` gives the focused tab's cwd as a fallback.

Chain: **ordered tab title → ai-title → sessionId → cwd**. That preserves window
grouping and left-to-right order while binding each tab to the right session.

## Requirements

- **Accessibility permission**: System Settings → Privacy & Security →
  Accessibility — enable your terminal / `osascript`. Needed to read the layout
  (save) and send keystrokes (resume).
- `python3` (stdlib only), and the Ghostty app.

## Restore behavior

Per tab:

- **claude** → `cd <cwd> && claude --resume <sessionId>` (exact conversation).
  `<cwd>` is the directory the session was launched from (where its transcript
  lives), not where it currently is: a session that entered a worktree resumes
  from the main checkout, so removing that worktree later doesn't pull the
  directory out from under the process.
- **vim** → `cd <cwd> && vim <file>`
- **app** → `cd <cwd> && <cmd>` for tabs titled with a known TUI (`hx` by
  default; set `GR_APPS="hx k9s ..."` when saving).
- **shell** → `cd <cwd>` only. The original command (e.g. `./deploy.sh`) is
  printed but **never auto-run**.

Tabs whose directory no longer exists (e.g. a removed worktree) are skipped with
a warning. Live sessions whose title didn't match any visible tab are listed in
the plan; pass `--unbound` to open them in an extra `(recovered sessions)`
window. That's off by default because they're usually stale or exiting.

`resume --go` drives Ghostty with synthetic keystrokes (`cmd+N` new window,
`cmd+T` new tab) and types each command, so **don't type in Ghostty while it
runs**. Env overrides:

| var         | default | meaning                            |
|-------------|---------|------------------------------------|
| `GR_DELAY`  | `0.8`   | seconds between actions (pacing)   |
| `GR_NEWWIN` | `n`     | new-window key (if you rebound it) |
| `GR_NEWTAB` | `t`     | new-tab key (if you rebound it)    |

## Known limits

- Tabs whose AI title hasn't been generated yet (shown as `Claude Code`) can't
  be title-bound; they reopen to the right directory but you pick the session.
- Directories with no transcript open with a plain `claude`.
- The tab→session binding can't be guaranteed for several Claude tabs launched
  from the *same* directory whose titles collide; the live session is preferred.

## License

Apache 2.0 — see [LICENSE](LICENSE).
