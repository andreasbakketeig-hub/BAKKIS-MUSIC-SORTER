# PyInstaller spec for BAKKIS MUSIC SORTER v2. Built by ../build_mac_app.sh; see ../PACKAGING.md.
#
# Windowed macOS .app targeting Apple Silicon (arm64) by default (or universal2 if configured).
# pywebview uses macOS system WebKit, so no heavy browser engines are bundled.
import os
import platform
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
APP_NAME = "BAKKIS MUSIC SORTER"
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "bakkis_sorter" / "__init__.py").read_text()).group(1)
IDENTITY = os.environ.get("CODESIGN_IDENTITY") or None
TARGET_ARCH = os.environ.get("BAKKIS_ARCH", platform.machine())

# Collect libsonare native dylib, signatures, and models
sonare_datas, sonare_binaries, sonare_hidden = collect_all("libsonare")

datas = [
    (str(ROOT / "bakkis_sorter" / "web"), "bakkis_sorter/web"),
] + sonare_datas

binaries = sonare_binaries
hiddenimports = ["webview.platforms.cocoa", "libsonare"] + sonare_hidden

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
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
    target_arch=TARGET_ARCH,
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
