# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build configuration for the packaged NASSync executable.
#   pyinstaller NASSync.spec        ->  dist/NASSync.exe
#
# console=False is deliberate: this is a GUI application, and a console
# window flashing up on every launch looks broken. nassync.cli redirects
# output to a log file when it finds no console attached.



a = Analysis(
    ['nassync_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='NASSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['docs/logo.ico'],
)
