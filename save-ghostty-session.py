#!/usr/bin/env python3
"""save-ghostty-session — snapshot Ghostty's windows/tabs in exact order, with
the precise Claude session running in each, so they can be restored verbatim.

Authoritative sources (no setup changes, no title hacking required):
  - ~/.claude/sessions/<pid>.json : per-process record {pid, sessionId, cwd,
    status,...}. Gives the exact live session id + cwd for every running tab.
  - transcript "ai-title" lines {type:"ai-title", aiTitle, sessionId}: the tab
    title Claude shows, keyed by session id -> lets us bind an Accessibility
    tab (which only exposes its title) to a session id.
  - Accessibility (osascript/System Events): window + tab order and titles, and
    per-window AXDocument (focused tab's cwd) as a fallback.

Result: each visual tab is bound to its exact session id + cwd, preserving
window grouping and left-to-right order. Tabs whose title Claude hasn't
summarised yet ("Claude Code"), or non-Claude tabs (vim/shell), are recorded by
title and best-effort cwd. Tabs running a known TUI (GR_APPS, default "hx")
are recorded as "app" and relaunched on restore.

A Claude tab's cwd is the directory the session was launched from (the one its
transcript lives under), not the process's current cwd: a session that entered
a worktree must be resumed from the main checkout, or the restored process ends
up living inside a worktree that may later be removed.

Output: ~/.ghostty-session/session.json  +  human summary on stdout.
Needs Accessibility permission (System Settings > Privacy & Security).
"""
import glob, json, os, re, subprocess, sys
from pathlib import Path

HOME = Path.home()
OUT_DIR = HOME / ".ghostty-session"
SESS_DIR = HOME / ".claude" / "sessions"
PROJECTS = HOME / ".claude" / "projects"
APPS = set(os.environ.get("GR_APPS", "hx").split())


def encode(path):
    """Claude's project-dir name for a cwd: every non-alphanumeric -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def transcript(sid):
    """The session's main transcript. A session can leave a small stub under a
    worktree's project dir on exit; the real one is the largest."""
    fps = glob.glob(str(PROJECTS / "*" / f"{sid}.jsonl"))
    return max(fps, key=os.path.getsize) if fps else None


def launch_cwd(sid, live_cwd):
    """Directory to resume `sid` from: the candidate cwd that encodes to the
    project dir holding its transcript, so `claude --resume` finds it there and
    the process doesn't start inside a worktree."""
    fp = transcript(sid)
    if not fp:
        return live_cwd
    proj = os.path.basename(os.path.dirname(fp))
    cands = [live_cwd]
    with open(fp) as f:
        for line in f:
            if '"worktree-state"' in line:
                try:
                    ws = json.loads(line).get("worktreeSession") or {}
                except Exception:
                    continue
                cands += [ws.get("originalCwd"), ws.get("preEnterOriginalCwd")]
            elif len(cands) < 4:
                m = re.search(r'"cwd":"([^"]+)"', line)
                if m:
                    cands.append(m.group(1))
    for c in cands:
        if c and encode(c) == proj and os.path.isdir(c):
            return c
    return live_cwd


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def live_sessions():
    """sid -> {pid, cwd, status, alive} for interactive sessions in the
    per-pid store (background/SDK workers have no tab to restore)."""
    out = {}
    for fp in glob.glob(str(SESS_DIR / "*.json")):
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        sid = d.get("sessionId")
        if sid and d.get("kind", "interactive") == "interactive":
            out[sid] = {"pid": d.get("pid"), "cwd": d.get("cwd"),
                        "status": d.get("status"), "alive": alive(d.get("pid"))}
    return out


def title_to_sid(prefer_live):
    """Map current tab title -> sessionId using ai-title lines.

    Builds sid -> latest aiTitle, then inverts. On title collision, prefer a
    session with a live process, else the most-recently-modified transcript.
    """
    sid2title, mtime = {}, {}
    for fp in sorted(glob.glob(str(PROJECTS / "*" / "*.jsonl")), key=os.path.getmtime):
        sid = os.path.basename(fp)[:-6]
        try:
            with open(fp) as f:
                for line in f:
                    if '"ai-title"' in line:
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") == "ai-title" and d.get("aiTitle"):
                            sid2title[sid] = d["aiTitle"]
                            mtime[sid] = os.path.getmtime(fp)
        except OSError:
            pass
    by_title = {}
    for sid, t in sid2title.items():
        by_title.setdefault(t, []).append(sid)
    out = {}
    for t, sids in by_title.items():
        live = [s for s in sids if s in prefer_live]
        pick = (live[0] if len(live) == 1 else
                max(live or sids, key=lambda s: mtime.get(s, 0)))
        out[t] = pick
    return out


def read_layout():
    """[{"window":title, "cwd":doc, "tabs":[title,...]}] in visual order."""
    src = r'''
    tell application "System Events"
      tell process "Ghostty"
        set out to ""
        repeat with w in windows
          set d to ""
          try
            set d to (value of attribute "AXDocument" of w)
          end try
          set out to out & "W|" & d & "|" & (name of w) & linefeed
          try
            repeat with t in (radio buttons of (tab group 1 of w))
              set out to out & "T|" & (name of t) & linefeed
            end repeat
          end try
        end repeat
        return out
      end tell
    end tell'''
    r = subprocess.run(["osascript", "-e", src], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Accessibility read failed: {r.stderr.strip()}\n"
                 "Enable: System Settings > Privacy & Security > Accessibility.")
    windows = []
    for line in r.stdout.splitlines():
        if line.startswith("W|"):
            _, doc, name = line.split("|", 2)
            cwd = doc[7:].rstrip("/") if doc.startswith("file://") else None
            if cwd:
                cwd = subprocess.run(["python3", "-c", "import urllib.parse,sys;print(urllib.parse.unquote(sys.argv[1]))", cwd], capture_output=True, text=True).stdout.strip()
            windows.append({"window": name, "cwd": cwd, "tabs": []})
        elif line.startswith("T|") and windows:
            windows[-1]["tabs"].append(line[2:])
    return windows


GLYPH = re.compile(r'^[^A-Za-z0-9/.~]+\s*')


def classify(title):
    t = GLYPH.sub("", title)
    m = re.search(r"(\S+)\s+\((~?[^)]*)\)\s*-\s*VIM", t)
    if m:
        return "vim", m.group(2).replace("~", str(HOME)), m.group(1)
    if re.match(r"^[\./~]", t) or re.search(r"\.sh(\s|$)", t):
        return "shell", None, t.strip()
    return "claude", None, t


def main():
    live = live_sessions()
    t2s = title_to_sid(set(live))

    layout = read_layout()
    windows = []
    bound = set()
    for w in layout:
        wd = {"window": GLYPH.sub("", w["window"]), "tabs": []}
        for raw in w["tabs"]:
            kind, cwd, clean = classify(raw)
            if kind == "claude" and clean not in t2s and clean in APPS:
                kind = "app"
            tab = {"title": clean, "kind": kind}
            if kind == "claude":
                sid = t2s.get(clean)
                tab["sessionId"] = sid
                live_cwd = live.get(sid, {}).get("cwd") if sid else None
                tab["cwd"] = (launch_cwd(sid, live_cwd) if sid else None) or w["cwd"]
                tab["alive"] = bool(sid and live.get(sid, {}).get("alive"))
                if sid:
                    bound.add(sid)
            elif kind == "vim":
                tab["cwd"] = cwd or w["cwd"]
                tab["file"] = clean
            else:  # shell / app
                tab["cwd"] = w["cwd"]
                tab["cmd"] = clean
            wd["tabs"].append(tab)
        windows.append(wd)

    unbound = [{"sessionId": s, "cwd": launch_cwd(s, i["cwd"])}
               for s, i in live.items() if i["alive"] and s not in bound]

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "session.json").write_text(
        json.dumps({"windows": windows, "unbound_live": unbound}, indent=2))

    print(f"Saved -> {OUT_DIR/'session.json'}\n")
    for i, w in enumerate(windows, 1):
        print(f"=== WINDOW {i}: {w['window']} ===")
        for j, t in enumerate(w["tabs"], 1):
            if t["kind"] == "claude":
                sid = t.get("sessionId")
                mark = (sid[:8] if sid else "??") + ("" if t.get("alive") else "·dead/new")
                print(f"  {j:>2}. (claude) {t['title'][:44]:44} [{mark}] {t.get('cwd') or '?'}")
            else:
                print(f"  {j:>2}. ({t['kind']:5}) {t['title'][:44]}")
    if unbound:
        print("\nLive sessions with no matching visible tab (restore with --unbound):")
        for u in unbound:
            print(f"  {u['sessionId'][:8]}  {u['cwd']}")


if __name__ == "__main__":
    main()
