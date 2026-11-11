# BAKKIS Music Sorter — Plan Registry

This registry tracks architectural specifications and feature plans. Each plan is persisted permanently on disk and loaded on-demand only when actively being developed or referenced.

| ID | Feature / Component | Status | Target Scope | Plan File |
| :--- | :--- | :--- | :--- | :--- |
| **PLN-01** | Streaming Folder Scanner | Completed | `library.py`, `backend/library.py`, `session.py` | [01_streaming_scanner.md](./archive/01_streaming_scanner.md) |
| **PLN-02** | Virtual Scrolling Queue UI | Completed | `web/queue.js`, `web/panels.js`, `web/app.js` | [02_virtual_queue_ui.md](./archive/02_virtual_queue_ui.md) |
| **PLN-03** | Waveform Range & BLOB Storage Engine | Completed | `waveform.py`, `storage/analysis.py`, `metadata.py`, `backend/settings.py` | [03_waveform_decoding.md](./archive/03_waveform_decoding.md) |
| **PLN-04** | Cover Art Low-Res Downsampler & LRU Cache | Completed | `metadata.py` | [04_cover_cache_lru.md](./archive/04_cover_cache_lru.md) |
| **PLN-05** | Session Modularization & Fast Tests | Completed | `session/`, `session.py`, `tests/session/` | [05_session_modularization.md](./archive/05_session_modularization.md) |
| **PLN-06** | Unified 50-Track Lookahead Warmup Engine | Completed | `pipeline.py`, `backend/`, `metadata.py` | [06_lookahead_warmup_pipeline.md](./archive/06_lookahead_warmup_pipeline.md) |
| **PLN-07** | Welcome Hub Redesign, In-App Comments Editor & Configurable Ratings | Completed | `web/`, `storage/reviews.py`, `promos/`, `config.py` | [07_welcome_hub_and_comments_editor.md](./archive/07_welcome_hub_and_comments_editor.md) |
| **PLN-08** | Settings Drawer Activation & Responsive Half-Screen Layout | Completed | `web/panels.js`, `web/app.css`, `web/index.html`, `web/app.js` | [08_settings_drawer_and_responsive_layout.md](./archive/08_settings_drawer_and_responsive_layout.md) |
| **PLN-09** | Half-Screen Width Constraint & Topbar Session Isolation | Completed | `web/index.html`, `web/app.css`, `web/app.js` | [09_half_screen_width_and_topbar_isolation.md](./archive/09_half_screen_width_and_topbar_isolation.md) |
| **PLN-10** | Promo Feedback History & Caught-Up Empty State (v2.1.3) | Completed | `storage/reviews.py`, `backend/promos.py`, `web/` | [10_reviewed_promos_history_and_empty_state.md](./archive/10_reviewed_promos_history_and_empty_state.md) |
| **PLN-11** | Backend & Storage Modularization (Phase 1) | Completed | `promos/session/`, `storage/` | [11_backend_and_storage_modularization.md](./archive/11_backend_and_storage_modularization.md) |
| **PLN-12** | Frontend Architecture & Style Modularization (Phase 2, v2.1.4) | Completed | `web/drawers/`, `web/css/` | [12_frontend_architecture_and_css_modularization.md](./archive/12_frontend_architecture_and_css_modularization.md) |
| **PLN-13** | Metadata, Config & Test Suite Segmentation (Phase 3, v2.1.5) | Completed | `metadata/`, `config/`, `tests/` | [13_metadata_config_and_test_segmentation.md](./archive/13_metadata_config_and_test_segmentation.md) |
| **PLN-14** | Dual-Workflow Onboarding & Getting Started Architecture | Completed | `README.md`, `web/`, `docs/plans/` | [14_workflow_onboarding_split.md](./archive/14_workflow_onboarding_split.md) |

---

### Status Definitions
- `Draft`: Initial concept under technical discovery.
- `Ready`: Detailed specification complete; ready for implementation and targeted testing.
- `In Progress`: Active development sprint.
- `Completed`: Implemented, verified against targeted test markers, and merged.
