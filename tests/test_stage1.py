"""Etapa 1: janela vazia, controller e pasta de dados fora do pacote."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from flow.paths import ensure_user_data, package_dir


class UserDataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_home = os.environ.get("HOME")

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()

    def test_folder_is_created_outside_the_package(self) -> None:
        os.environ["HOME"] = str(self.root)
        data = ensure_user_data()
        self.assertTrue(data.is_dir())
        self.assertFalse(package_dir().resolve() == data or package_dir().resolve() in data.parents)
        self.assertIn("Flow", data.parts)

    def test_refuses_a_folder_inside_the_package(self) -> None:
        inside = package_dir() / "dados"
        with self.assertRaises(ValueError):
            ensure_user_data(inside)

    def test_recreating_the_package_keeps_the_folder(self) -> None:
        data = ensure_user_data(self.root / "dados")
        marker = data / "marker.txt"
        marker.write_text("fica", encoding="utf-8")
        (data / "documentos").mkdir()
        (data / "documentos" / "nota.txt").write_text("arquivo", encoding="utf-8")

        project = self.root / "projeto"
        dist = project / "dist"
        dist.mkdir(parents=True)
        (dist / "Flow.app").mkdir()
        shutil.rmtree(dist)
        dist.mkdir()

        again = ensure_user_data(data)
        self.assertEqual(again, data)
        self.assertEqual(marker.read_text(encoding="utf-8"), "fica")
        self.assertEqual((data / "documentos" / "nota.txt").read_text(encoding="utf-8"), "arquivo")
        self.assertFalse(dist.resolve() == data or dist.resolve() in data.parents)


class WindowTest(unittest.TestCase):
    def test_empty_window_talks_only_through_the_controller(self) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        for banned in ("sqlite", "imap", "smtp", "openai", "baileys"):
            self.assertNotIn(banned, qml)

        from flow.window import open_window

        with tempfile.TemporaryDirectory() as name:
            app, engine, controller = open_window(Path(name))
            roots = engine.rootObjects()
            self.assertTrue(roots)
            self.assertEqual(roots[0].property("title"), "flow")
            self.assertIs(engine.rootContext().contextProperty("controller"), controller)
            self.assertTrue(Path(controller.dataDirectory).is_dir())
            app.quit()
