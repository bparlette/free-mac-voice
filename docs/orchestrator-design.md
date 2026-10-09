# Phone-controlled AI orchestrator on the Mac (design, 2026-10-08)

Goal: from the phone, hand a task to one place and have it run on the Mac by the right agent: Claude Code, Antigravity,
Meta Muse (in the browser) or Hark Handoff (in the browser), with progress and approvals coming back to the phone.

Status: **design only.** Nothing below is switched on yet except the Claude Code Remote Control server that already starts at login.
Findings and what to measure are recorded in `benchmarks/README.md`, section L.

## 1. What each tool offers (checked 2026-10-08)

| Tool | Runs where | Phone control | Can another program drive it? | Limits |
|---|---|---|---|---|
| Claude Code | Mac (CLI) | **Remote Control**: `claude remote-control` (server mode) shows up in the Claude app / claude.ai/code; you can start new sessions from the phone, get push notifications, answer permission prompts | Yes: it is the hub (shell, files, MCP, browser extension) | Outbound HTTPS only. Server mode exits after ~10 min without network (launchd restarts it). `--spawn worktree` isolates sessions; default `same-dir` shares one folder |
| Antigravity (IDE) | Mac (desktop app) | **Antigravity Remote Control** (rolled out 2026-08-21): join sessions already open on the Mac, start a task, review plans and artifacts, answer prompts, push notifications | Yes, locally: `$HOME/.gemini/antigravity/bin/agentapi` has `new-conversation [--model=flash_lite\|flash\|pro] [--title=] <prompt>`, `send-message <id> <text>`, `get-conversation-metadata <id>`. The `agy` CLI (`agy -p "<task>"`) is the headless alternative (not installed) | Desktop app must be running and signed in |
| Meta Muse | Meta's cloud VM; used through muse.ai in Chrome (and the Muse phone app) | Its own app | **No public API for individuals** (a Muse API is announced for the Meta Enterprise Platform). Only via the browser: an agent types into the muse.ai tab and reads the reply | Muse's own Sentinel must approve anything it sends out; it acts on your Meta-linked accounts |
| Hark Handoff | Hark's cloud; used through its web app in Chrome | Its own web app | **No public API** (research preview / waitlist). Browser only, same pattern as Muse | Access not confirmed for this account |

## 2. Architecture

```
 Phone
  ├─ Claude app ──(Remote Control, outbound HTTPS)──► Claude Code "orchestrator" server on the Mac   ◄── primary console
  ├─ Antigravity app ──(Remote Control)──► Antigravity desktop sessions                                ◄── watch / approve Antigravity work
  └─ Muse app ──► Muse approvals (Sentinel)                                                            ◄── approve Muse actions

 Mac
  Claude Code orchestrator (claude remote-control --spawn worktree, cwd $HOME/orchestrator)
   ├─ worker: Claude Code itself ........ repos, shell, files, research
   ├─ worker: Antigravity ............... agentapi new-conversation / send-message / get-conversation-metadata
   ├─ worker: Muse (browser) ............ Claude in Chrome drives the muse.ai tab in a dedicated Chrome profile
   ├─ worker: Hark Handoff (browser) .... same pattern, once there is access
   └─ shared state: $HOME/orchestrator/tasks/<id>/{task.md,status.json,result.md}  +  tasks.jsonl (one line per task)
```

One hub, not four: the phone talks to the Claude Code orchestrator, which decides who does the work, tracks it in the task
folder and reports back in the same phone conversation. The Antigravity and Muse apps on the phone are only for watching and
approving work that those tools already show natively.

## 3. Routing rules (the orchestrator's CLAUDE.md)

| Task | Worker | Why |
|---|---|---|
| Change code in a repo, run tests, Mac shell work | Claude Code (in its own worktree) | Has the files and the tools; follows AI_WORKFLOW.md |
| Same, but a second opinion or a parallel attempt is wanted | Antigravity via `agentapi` (Gemini models) | Independent model; results reviewable in Antigravity Remote Control |
| Research and summaries | Claude Code | Web search plus files |
| Errands on real websites (order, book, compare prices, fill forms) | Hark Handoff if available, else Muse | Purpose-built browser agents |
| Personal tasks tied to Meta accounts or email/calendar connected to Muse | Muse | It holds those connections |
| Anything that pays, sends messages to people, or deletes data | Any worker, but **stop and ask on the phone first** | Hard rule, never automatic |

## 4. Rules that keep it safe

- **Never work in the live checkouts.** The voice assistant runs from its repo; the orchestrator lives in `$HOME/orchestrator`, and code
  workers use git worktrees and `<assistant>/<topic>` branches, merging by the AI_WORKFLOW.md rules.
- **One writer per repo at a time**: the orchestrator records which worker holds which repo in `tasks.jsonl`.
- **Browser agents get their own Chrome profile**, signed in only to Muse / Hark; no passwords or cards are handed to the orchestrator.
- **Outbound only.** Claude Code and Antigravity Remote Control both connect out; no ports are opened on the router.
- **Approvals stay on the phone**: Claude push "when actions required", Antigravity push, Muse Sentinel.
- **Public repos stay clean**: task folders live outside the repos; no personal data is committed.

## 5. Setup steps (when approved)

1. Make the orchestrator folder and its `CLAUDE.md` (the routing table above, the task-folder format, the stop-and-ask rule).
2. Change the existing Claude Code Remote Control login agent (in `~/Library/LaunchAgents`) to
   `claude remote-control --name "Mac orchestrator" --spawn worktree --permission-mode default` with `WorkingDirectory` = `$HOME/orchestrator`
   (today it runs in the voice-assistant repo with the shared `same-dir` mode).
3. In Claude Code `/config`: enable **Push when actions required**.
4. Antigravity: sign in on the phone app and approve the one-time host verification for Remote Control.
5. Install **Claude in Chrome** in a dedicated Chrome profile; sign that profile in to muse.ai (and Hark when there is access).
6. Add a small `dispatch` skill to the orchestrator: create the task folder, call the chosen worker, poll, write `result.md`, report.
7. Optional: voice assistant command "Mac, ask the orchestrator to ..." appends to `tasks.jsonl`.

## 6. What to measure once it runs (goes into benchmarks section L)

Phone message -> first reply (s); task completed without help, per worker (%); approvals asked per task; tasks that touched the
wrong folder (must be 0); cost per task where a worker reports it.
