# -*- mode: python ; coding: utf-8 -*-
"""Сборка помощника: py -m PyInstaller AvatariaHelper.spec

onedir, а не onefile: templates/ весит под шестьдесят мегабайт, и onefile
распаковывал бы их во временную папку при каждом запуске. Папку целиком
заворачивает в один exe установщик (installer.iss).
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # Картинки и шрифты ложатся внутрь сборки, откуда их берёт app.core.paths.
    datas=[("templates", "templates"), ("assets", "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AvatariaHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AvatariaHelper",
)
