# -*- mode: python ; coding: utf-8 -*-
"""Um processo. .app no macOS, .exe no Windows. Sem assinatura e sem instalador."""

import sys
from pathlib import Path

root = Path(SPECPATH)
qml = root / "src" / "flow" / "qml"

a = Analysis(
    [str(root / "src" / "flow" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(qml), "flow/qml")],
    hiddenimports=[
        "flow.window",
        "flow.controller",
        "flow.paths",
        "flow.store",
        "flow.queue",
        "flow.classify",
        "flow.mail",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Flow",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Flow",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Flow.app",
        icon=None,
        bundle_identifier="com.sibinel.flow",
        info_plist={
            "CFBundleName": "Flow",
            "CFBundleDisplayName": "Flow",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
