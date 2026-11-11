# BAKKIS MUSIC SORTER — Versioning Policy

BAKKIS MUSIC SORTER follows standard Semantic Versioning (`MAJOR.MINOR.PATCH`):

```
MAJOR . MINOR . PATCH
  3   .   0   .   1
```

## Increment Guidelines

### 1. `MAJOR` (First Number)
* **When to increment**:
  - Major architectural overhauls (e.g. v1 Tkinter to v2 pywebview, or v2 to v3 modularized engine).
  - Breaking changes to database schema without automatic backward migrations.
  - Significant workflow or platform changes affecting core file behavior.
* **Effect**: Resets `MINOR` and `PATCH` to `0` (e.g., `2.1.5` → `3.0.0`).

### 2. `MINOR` (Second Number)
* **When to increment**:
  - Substantial new functional capability (e.g. adding Trackstack support alongside Inflyte).
  - New standalone drawers, panels, or full-featured settings views.
  - New audio analysis tools or background pipelines (e.g. `libsonare` drop detector).
* **Effect**: Resets `PATCH` to `0` (e.g., `3.0.1` → `3.1.0`).

### 3. `PATCH` (Third Number)
* **When to increment**:
  - Bug fixes, regression repairs, and stability improvements.
  - UI/UX polish, copy adjustments, layout alignments, and styling tweaks.
  - Workflow ordering updates (e.g. Welcome Hub card hierarchy, done-screen button isolation).
  - Minor performance improvements and test suite expansions.
* **Effect**: Increments the third digit (e.g., `3.0.0` → `3.0.1`).

---

## Enforcement
Every user-facing update, pull request, or release must update `__version__` in `bakkis_sorter/__init__.py` and the corresponding test assertion in `tests/test_web_modules.py`.
