# AI collaboration workflow

This repo is developed by more than one AI coding assistant working in parallel.
The owner does not review or approve merges. Follow these rules so work lands on
`main` safely without human involvement.

## Branches
- Never commit directly on `main` while another assistant may have uncommitted work.
- Do your work on your own branch: `<assistant>/<short-topic>` (e.g. `gemini/critic-tts`, `grokbot/review-batch-2`).
- Keep commits small and focused, one fix or feature per commit.

## Merging to main (automatic, no human approval)
1. Commit your own work. Never stage or commit another assistant's uncommitted changes.
2. `git fetch origin` and bring your branch up to date with `origin/main` (rebase or merge).
3. Resolve any conflicts yourself, preserving both assistants' intent.
4. Run the full validation on macOS with the project venv:
   ```bash
   ./.venv/bin/python -c 'import ast; ast.parse(open("free_voice.py").read()); ast.parse(open("masterpiece_critic.py").read()); print("SYNTAX OK")'
   ./.venv/bin/python -m unittest discover tests
   ./.venv/bin/python -m unittest discover tests/personal   # if present locally
   ```
5. Only if everything passes: fast-forward `main` to your branch and `git push origin main`.
6. If anything fails, do not merge. Fix it on your branch and repeat.

## Never
- Force-push, rewrite published history, or delete another assistant's branch.
- Commit personal data. This repo is public: no usernames, home-directory paths,
  real names, device names, IP addresses, API keys, or tokens. Use `$HOME` and
  placeholders; keep secrets in the local `.env` (gitignored).
- Use `sudo`, create a new venv, or `pip install` globally.
- Rewrite whole large files. Make targeted edits.
- Kill or restart running services unless asked.

## Project invariants
- The companion overlay stays click-through (`ignoresMouseEvents = True`).
- The couch base sprite stays pinned at `(0, 0)`.
- AppKit/UI work runs on the main thread (`run_on_main`); audio, TTS and network
  calls run on background threads.
