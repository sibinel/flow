# -*- mode: python ; coding: utf-8 -*-
"""Um processo. .app no macOS, .exe no Windows. Sem assinatura e sem instalador.

O Node e os pacotes da ponte do WhatsApp vão dentro do pacote. Gere com
scripts/build_app.sh, que baixa o Node em build/node-runtime e roda npm ci.
"""

import sys
from pathlib import Path

root = Path(SPECPATH)
qml = root / "src" / "flow" / "qml"
baileys = root / "src" / "flow" / "baileys"
windows = sys.platform == "win32"
node_runtime = root / "build" / "node-runtime"
node_exe = node_runtime / ("node.exe" if windows else "bin/node")
node_modules = baileys / "node_modules"
for required in (node_exe, node_modules / "@whiskeysockets" / "baileys"):
    if not required.exists():
        raise SystemExit(f"falta {required}; gere o pacote com scripts/build_app.sh")

a = Analysis(
    [str(root / "src" / "flow" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[(str(node_exe), "flow/node" if windows else "flow/node/bin")],
    datas=[
        (str(qml), "flow/qml"),
        (str(baileys / "bridge.mjs"), "flow/baileys"),
        (str(baileys / "package.json"), "flow/baileys"),
        (str(node_modules), "flow/baileys/node_modules"),
    ],
    hiddenimports=[
        "flow.window",
        "flow.controller",
        "flow.paths",
        "flow.store",
        "flow.queue",
        "flow.classify",
        "flow.mail",
        "flow.send",
        "flow.whatsapp",
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
