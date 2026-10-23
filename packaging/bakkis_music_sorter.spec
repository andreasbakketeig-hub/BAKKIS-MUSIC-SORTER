# PyInstaller spec for BAKKIS MUSIC SORTER v2. Built by ../build_mac_app.sh; see ../PACKAGING.md.
#
# A universal2 (Apple Silicon + Intel) windowed .app. pywebview uses the system WebKit, so no browser
# or audio player is bundled. Set CODESIGN_IDENTITY to sign with a Developer ID (hardened runtime).
import os
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP_NAME = "BAKKIS MUSIC SORTER"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "bakkis_sorter" / "__init__.py").read_text()).group(1)
IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "bakkis_sorter" / "web"), "bakkis_sorter/web")],
    hiddenimports=["webview.platforms.cocoa"],
    excludes=["tkinter", "PIL", "pytest", "PyInstaller", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    target_arch="universal2",
    codesign_identity=IDENTITY,
    entitlements_file=str(ROOT / "packaging" / "entitlements.plist") if IDENTITY else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ROOT / "assets" / "icon" / "AppIcon.icns"),
    bundle_identifier="com.bakkis.music-sorter",
    version=VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© 2026 Andreas Bakketeig (BAKKIS). MIT License.",
    },
)
