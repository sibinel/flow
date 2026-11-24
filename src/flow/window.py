"""Sobe o processo: a fila em QML, ligada só ao controller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from flow.controller import Controller


def _theme() -> None:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_THEME", "Dark")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_ACCENT", "Brown")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_PRIMARY", "Brown")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_BACKGROUND", "#1c1917")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_FOREGROUND", "#fafaf9")


def qml_file() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS"))
        return base / "flow" / "qml" / "Main.qml"
    return Path(__file__).resolve().parent / "qml" / "Main.qml"


def open_window(data_root: Path | None = None):
    _theme()
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    controller = Controller(data_root)
    controller.watch_schedule()
    engine.rootContext().setContextProperty("controller", controller)
    engine.load(qml_file())
    return app, engine, controller


def main() -> int:
    app, engine, _controller = open_window()
    if not engine.rootObjects():
        return 1
    return app.exec()
