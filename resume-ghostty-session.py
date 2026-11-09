#!/usr/bin/env python3
"""resume-ghostty-session — rebuild the Ghostty windows/tabs captured by
save-ghostty-session.py, in the same window grouping and left-to-right order,
each tab cd'd to its directory and resumed to the exact session.

Usage:
  resume-ghostty-session.py            # dry-run: print the plan
  resume-ghostty-session.py --go       # drive Ghostty (needs Accessibility)
  --unbound   also open live sessions that had no visible tab, in an extra
              window (off by default: these are usually stale or exiting)

Per tab:
  claude -> cd <cwd> && claude --resume <sessionId>   (exact conversation)
  vim    -> cd <cwd> && vim <file>
  app    -> cd <cwd> && <cmd>   (known TUIs such as hx; see GR_APPS in save)
  shell  -> cd <cwd>   (the original command is shown but NOT auto-run)

Tabs whose directory no longer exists (e.g. a removed worktree) are skipped
with a warning, and windows left with no tabs are not opened.

Mechanism: Ghostty has no API to create a tab running a command, so this uses
System Events keystrokes (cmd+N new window, cmd+T new tab) and types each
command. If you've rebound those keys, set GR_NEWWIN / GR_NEWTAB.
Tunable: GR_DELAY (seconds between actions, default 0.8).
"""
import json, os, subprocess, sys, time
from pathlib import Path

SNAP = Path.home() / ".ghostty-session" / "session.json"
DELAY = float(os.environ.get("GR_DELAY", "0.8"))
NEWWIN = os.environ.get("GR_NEWWIN", "n")
NEWTAB = os.environ.get("GR_NEWTAB", "t")


def osa(src):
    r = subprocess.run(["osascript", "-e", src], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


def esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def sh_quote(s):
    return "'" + (s or "").replace("'", "'\\''") + "'"


def cmd_key(letter):
    osa(f'tell application "System Events" to keystroke "{letter}" using command down')


def type_line(text):
    osa(f'tell application "System Events" to keystroke "{esc(text)}"')
    osa('tell application "System Events" to key code 36')  # Return


def tab_command(tab):
    cwd = tab.get("cwd")
    if tab["kind"] == "claude":
        sid = tab.get("sessionId")
        base = f"cd {sh_quote(cwd)} && " if cwd else ""
        return base + (f"claude --resume {sid}" if sid else "claude")
    if tab["kind"] == "app":
        return f"cd {sh_quote(cwd)} && {tab['cmd']}" if cwd else tab["cmd"]
    if tab["kind"] == "vim":
        return f"cd {sh_quote(cwd)} && vim {sh_quote(tab.get('file',''))}"
    # shell: only cd; never auto-run the captured command
    return f"cd {sh_quote(cwd)}" if cwd else ":"


def main():
    go = "--go" in sys.argv
    if not SNAP.exists():
        sys.exit(f"No snapshot at {SNAP}. Run save-ghostty-session.py first.")
    snap = json.loads(SNAP.read_text())
    windows = snap.get("windows", [])
    unbound = snap.get("unbound_live", [])
    if unbound and "--unbound" in sys.argv:
        windows = windows + [{"window": "(recovered sessions)",
                              "tabs": [{"kind": "claude", "sessionId": u["sessionId"],
                                        "cwd": u["cwd"], "title": u["sessionId"][:8]}
                                       for u in unbound]}]

    skipped = []
    kept = []
    for w in windows:
        tabs = []
        for t in w["tabs"]:
            if t.get("cwd") and not os.path.isdir(t["cwd"]):
                skipped.append((w["window"], t))
            else:
                tabs.append(t)
        if tabs:
            kept.append({**w, "tabs": tabs})
    windows = kept

    # plan
    print(f"Plan: {len(windows)} window(s)"
          f"{' — DRY RUN (pass --go)' if not go else ''}\n")
    for i, w in enumerate(windows, 1):
        print(f"WINDOW {i}: {w['window']}")
        for j, t in enumerate(w["tabs"], 1):
            note = "" if (t["kind"] != "shell") else "   # cd only; run manually:"
            print(f"  {j:>2}. {tab_command(t)}{note}")
            if t["kind"] == "shell":
                print(f"       (was: {t.get('cmd','')})")
    if skipped:
        print("\nSkipped (directory no longer exists):")
        for wname, t in skipped:
            print(f"  [{wname}] {t.get('title','')}: {tab_command(t)}")
    if unbound and "--unbound" not in sys.argv:
        print(f"\n{len(unbound)} live session(s) had no visible tab; pass --unbound to open them:")
        for u in unbound:
            print(f"  {u['sessionId'][:8]}  {u['cwd']}")
    if not go:
        print("\nRe-run with --go to rebuild it in Ghostty.")
        return

    osa('tell application "Ghostty" to activate')
    time.sleep(DELAY)
    for wi, w in enumerate(windows):
        cmd_key(NEWWIN)
        time.sleep(DELAY * 1.5)
        for ti, t in enumerate(w["tabs"]):
            if ti > 0:
                cmd_key(NEWTAB)
                time.sleep(DELAY)
            type_line(tab_command(t))
            time.sleep(DELAY)
    print(f"\nRebuilt {len(windows)} window(s). Titles repopulate as sessions load.")


if __name__ == "__main__":
    main()
