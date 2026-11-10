"""Tests for Frontend ES Modules resolution, static serving, and MIME types."""

import http.client
import re
from pathlib import Path
import pytest

from bakkis_sorter.api import AppBackend
from bakkis_sorter.server import AppServer

pytestmark = pytest.mark.frontend

WEB_DIR = Path(__file__).resolve().parent.parent / "bakkis_sorter" / "web"

EXPECTED_MODULES = [
    "bus.js",
    "api.js",
    "state.js",
    "player.js",
    "wave.js",
    "cards.js",
    "decisions.js",
    "gestures.js",
    "keyboard.js",
    "stash.js",
    "queue.js",
    "toasts.js",
    "folder.js",
    "comments.js",
    "panels.js",
    "drawers/common.js",
    "drawers/settings.js",
    "drawers/presets.js",
    "drawers/history.js",
    "drawers/promo_history.js",
    "drawers/modals.js",
    "app.js",
]

EXPECTED_CSS = [
    "app.css",
    "css/base.css",
    "css/player.css",
    "css/hub.css",
    "css/drawers.css",
    "css/responsive.css",
]


def read_full_css() -> str:
    """Read app.css and recursively expand any relative @import statements."""
    root_css = (WEB_DIR / "app.css").read_text(encoding="utf-8")
    import_re = re.compile(r"""@import\s+['"](\.[^'"]+)['"];?""")
    parts = []
    for line in root_css.splitlines():
        match = import_re.match(line.strip())
        if match:
            target = (WEB_DIR / match.group(1)).resolve()
            assert target.is_file(), f"Imported stylesheet {target} does not exist"
            parts.append(target.read_text(encoding="utf-8"))
        else:
            parts.append(line)
    return "\n".join(parts)


def test_all_frontend_modules_exist() -> None:
    for mod in EXPECTED_MODULES:
        p = WEB_DIR / mod
        assert p.is_file(), f"Expected ES module {mod} was not found in {WEB_DIR}"
        assert p.stat().st_size > 50, f"Module {mod} is unexpectedly empty"

    for css in EXPECTED_CSS:
        p = WEB_DIR / css
        assert p.is_file(), f"Expected stylesheet {css} was not found in {WEB_DIR}"
        assert p.stat().st_size > 50, f"Stylesheet {css} is unexpectedly empty"


def test_es_module_relative_imports_resolve() -> None:
    """Verify that every `import ... from './xyz.js'` targets a valid, existing file on disk."""
    import_re = re.compile(r"""(?:import|export)\s+.*?\s+from\s+['"](\.\.?\/[^'"]+)['"]""")
    for mod in EXPECTED_MODULES:
        mod_file = WEB_DIR / mod
        content = mod_file.read_text(encoding="utf-8")
        matches = import_re.findall(content)
        for target in matches:
            resolved = (mod_file.parent / target).resolve()
            assert resolved.is_file(), (
                f"In {mod}: relative import '{target}' failed to resolve to a valid file ({resolved})"
            )


def test_index_html_loads_app_and_panels_as_es_module() -> None:
    index_html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/static/app.js"></script>' in index_html
    assert '<script type="module" src="/static/panels.js"></script>' in index_html


def test_static_server_serves_all_modules_with_correct_mime() -> None:
    """Verify server serves each JS and CSS module with correct MIME and charset=utf-8."""
    backend = AppBackend()
    server = AppServer(backend).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        for mod in EXPECTED_MODULES:
            conn.request("GET", f"/static/{mod}")
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200, f"Failed to fetch /static/{mod}: {resp.status}"
            ctype = resp.getheader("Content-Type", "")
            assert "javascript" in ctype, f"Invalid Content-Type for {mod}: {ctype}"
            assert "charset=utf-8" in ctype.lower()
            assert len(body) > 50

        for css in EXPECTED_CSS:
            conn.request("GET", f"/static/{css}")
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200, f"Failed to fetch /static/{css}: {resp.status}"
            ctype = resp.getheader("Content-Type", "")
            assert "text/css" in ctype, f"Invalid Content-Type for {css}: {ctype}"
            assert "charset=utf-8" in ctype.lower()
            assert len(body) > 50
        conn.close()
    finally:
        server.stop()
        backend.close()


def test_panels_exposes_window_controllers() -> None:
    """Verify that panels.js exports controller objects onto window so app.js can invoke them."""
    panels_code = (WEB_DIR / "panels.js").read_text(encoding="utf-8")
    for prop in [
        "window.Drawers",
        "window.SettingsDrawer",
        "window.HistoryDrawer",
        "window.PromoHistoryDrawer",
        "window.PresetEditor",
        "window.Guide",
        "window.Welcome",
    ]:
        assert f"{prop} = " in panels_code, f"panels.js must export {prop} onto window"


def test_responsive_half_screen_css_rules() -> None:
    """Verify bundled styles contain half-screen breakpoints, 2x2 tile layout, and pinned controls."""
    css = read_full_css()
    # Pinned top icons
    assert "margin-left: auto" in css
    # Half-screen clamp
    assert "clamp(540px" in css
    # Responsive swipe 2x2 grid
    assert "repeat(2, 1fr)" in css
    # Media queries for 960px, 768px, 640px
    assert "@media (max-width: 960px)" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 640px)" in css
    # Full-width drawer on small screens
    assert ".drawer { width: 100vw; }" in css


def test_home_navigation_and_topbar_isolation() -> None:
    """Verify home navigation, body column constraint, and topbar promo isolation."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-home"' in html
    assert 'id="brand-home"' in html

    css = read_full_css()
    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "max-width: 100vw" in css
    assert ".btn-home" in css

    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "export async function goHome()" in js
    assert "window.goHome = goHome" in js
    assert "btn-home" in js
    assert "brand-home" in js
    assert "close_session" in js


def test_promo_caught_up_and_history_drawer() -> None:
    """Verify v2.1.4 version, promo caught-up empty state, and history drawer markup & controllers."""
    import bakkis_sorter

    assert bakkis_sorter.__version__ == "3.0.0"

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="promo-caught-up"' in html
    assert 'id="drawer-promo-history"' in html
    assert 'id="btn-promo-history"' in html
    assert 'id="btn-caught-up-history"' in html

    css = read_full_css()
    assert ".promo-caught-up-banner" in css
    assert ".promo-hist-card" in css
    assert ".promo-hist-search-input" in css

    panels = (WEB_DIR / "panels.js").read_text(encoding="utf-8")
    assert "PromoHistoryDrawer" in panels
    assert "window.PromoHistoryDrawer = PromoHistoryDrawer" in panels

    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "btn-promo-history" in app_js
    assert "btn-caught-up-history" in app_js
    assert "promo-caught-up" in app_js


def test_settings_drawer_includes_promo_section_and_actions() -> None:
    """Verify that settings.js includes the Music Promos section, comments dialog launcher, and promo history launcher."""
    settings_code = (WEB_DIR / "drawers" / "settings.js").read_text(encoding="utf-8")
    assert "CommentsModal" in settings_code
    assert 'section: "Music Promos (Inflyte & Trackstack)"' in settings_code
    assert 'type: "promo_settings"' in settings_code
    assert "btn-settings-comments" in settings_code
    assert "btn-settings-promo-history" in settings_code
    assert "renderPromoSettings" in settings_code

