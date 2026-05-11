# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('/Users/andreasbakketeig/Desktop/BAKKIS MUSIC SORTER/genre_labels.json', '.'), ('/Users/andreasbakketeig/Desktop/BAKKIS MUSIC SORTER/bakkis_settings.json', '.'), ('/Users/andreasbakketeig/Desktop/BAKKIS MUSIC SORTER/bakkislogo.png', '.')]
binaries = [('/usr/local/bin/ffmpeg', '.')]
hiddenimports = ['PIL._tkinter_finder']
tmp_ret = collect_all('mutagen')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pillow')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['/Users/andreasbakketeig/Desktop/BAKKIS MUSIC SORTER/bakkis_music_sorter.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BAKKIS MUSIC SORTER',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BAKKIS MUSIC SORTER',
)
app = BUNDLE(
    coll,
    name='BAKKIS MUSIC SORTER.app',
    icon=None,
    bundle_identifier='com.bakkis.music-sorter',
)
