# PLN-14: Dual-Workflow Onboarding & Getting Started Architecture

## Summary
Deconstruct and clearly delineate the two primary user workflows in BAKKIS MUSIC SORTER:
1. **Workflow 1: Local Music Sorter (Primary / Start Here)**: Choose folder, drag & drop, Move vs. Copy safety, instant playback jump point, 1-key sorting (Keep, Archive, Favorite, Skip) or Grid mode (1–9).
2. **Workflow 2: Unified Promo Review (Inflyte & Trackstack)**: Authenticate once via WKWebView Keychain, configure comment rotation pools and star ratings, 1-key feedback dispatch, offline travel stash, clean downloads straight to `~/Downloads` with zero disk moving.

## Architectural Changes
- **`README.md`**: Updated `## Getting started` with a side-by-side workflow comparison table, followed by detailed step-by-step guides for Workflow 1 (Local Sorter) first and Workflow 2 (Promo Reviewer) second.
- **`web/index.html`**: Swapped Welcome Hub cards so `welcome-card-sorter` (Pillar 1) precedes `welcome-card-promos` (Pillar 2). Promoted Sorter CTA to `primary-btn`. Updated first-launch welcome modal to introduce both workflows with sorting first.
- **`web/css/hub.css`**: Enhanced visual differentiation for the Sorter CTA and added `.welcome-promo-btn` teal accent styling.
- **`web/drawers/modals.js`**: Re-organized keyboard shortcuts dialog (`Guide.show()`) into distinct workflow groupings: `Workflow 1: Local Sorter`, `Workflow 2: Promo Reviewer`, `Playback`, and `General`.
