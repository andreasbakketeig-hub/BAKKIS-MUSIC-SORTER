# CLAUDE.md

Read `PLAN.md` first — it holds the agreed rebuild plan, decisions, UX spec and full parity checklist.

- Talk to Andreas in Norwegian. Code, comments, commit messages, UI and docs in English (no other UI languages).
- v1 is `bakkis_music_sorter.py` (Tkinter + mpv). Keep it runnable until v2 reaches parity; use it as the reference for behavior.
- v2 lives in `bakkis_sorter/` on branch `v2-pywebview`. macOS only.
- Never run sorting/moving code against real music folders in tests; use temp dirs.
- Speed matters: never block the UI on file operations or audio loading.
