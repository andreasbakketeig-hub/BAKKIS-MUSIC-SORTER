# CLAUDE.md

Read `PLAN.md` first — it holds the agreed rebuild plan, decisions, UX spec and full parity checklist.

- Talk to Andreas in Norwegian. Code, comments, commit messages, UI and docs in English (no other UI languages).
- v1 (Tkinter + mpv) is retired and kept in `legacy/` for reference only; v2 has full parity. Don't develop v1.
- v2 lives in `bakkis_sorter/` on `main` (released as 2.0.x on GitHub). macOS only.
- Never run sorting/moving code against real music folders in tests; use temp dirs.
- Speed matters: never block the UI on file operations or audio loading.
