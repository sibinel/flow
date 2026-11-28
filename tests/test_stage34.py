"""Etapa 34: o app empacotado acha o Node e os pacotes da ponte."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow import whatsapp
from flow.paths import package_dir, whatsapp_session
from flow.whatsapp import _bridge_script, invoke, node_executable

_ROOT = package_dir().parents[1]


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class NodeLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "bundle"
        self.data = self.root / "data"
        self.data.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _frozen(self):
        return patch.multiple(whatsapp.sys, frozen=True, _MEIPASS=str(self.bundle), create=True)

    def test_bundled_node_comes_first(self) -> None:
        bundled = _executable(self.bundle / "flow" / "node" / "bin" / "node")
        _executable(self.data / "node" / "bin" / "node")
        with self._frozen(), patch("flow.whatsapp.shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(node_executable(self.data), str(bundled))

    def test_user_folder_before_path(self) -> None:
        local = _executable(self.data / "node" / "bin" / "node")
        with patch("flow.whatsapp.shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(node_executable(self.data), str(local))

    def test_path_is_the_last_resort(self) -> None:
        with patch("flow.whatsapp.shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(node_executable(self.data), "/usr/bin/node")
        with patch("flow.whatsapp.shutil.which", return_value=None):
            self.assertIsNone(node_executable(self.data))

    def test_file_without_execute_permission_is_skipped(self) -> None:
        broken = self.data / "node" / "bin" / "node"
        broken.parent.mkdir(parents=True)
        broken.write_text("", encoding="utf-8")
        broken.chmod(0o644)
        with patch("flow.whatsapp.shutil.which", return_value="/usr/bin/node"):
            self.assertEqual(node_executable(self.data), "/usr/bin/node")

    def test_windows_uses_node_exe_without_bin(self) -> None:
        bundled = _executable(self.bundle / "flow" / "node" / "node.exe")
        with (
            self._frozen(),
            patch.object(whatsapp.sys, "platform", "win32"),
            patch("flow.whatsapp.os.access", return_value=True),
        ):
            self.assertEqual(node_executable(self.data), str(bundled))

    def test_frozen_bridge_sits_next_to_its_node_modules(self) -> None:
        with self._frozen():
            script = _bridge_script()
        self.assertEqual(script, self.bundle / "flow" / "baileys" / "bridge.mjs")
        self.assertEqual(script.parent / "node_modules", self.bundle / "flow" / "baileys" / "node_modules")

    def test_invoke_runs_the_resolved_node_in_the_bridge_folder(self) -> None:
        local = _executable(self.data / "node" / "bin" / "node")
        session = whatsapp_session(self.data)
        session.mkdir(parents=True)
        (session / "creds.json").write_text(json.dumps({"registered": True}), encoding="utf-8")
        calls: list = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs["cwd"]))
            return subprocess.CompletedProcess(args, 0, b'{"messages": []}', b"")

        with (
            patch("flow.whatsapp.shutil.which", return_value=None),
            patch("flow.whatsapp.subprocess.run", fake_run),
        ):
            self.assertEqual(invoke(session, "read", {}), {"messages": []})
        self.assertEqual(Path(calls[0][0][0]).resolve(), local.resolve())
        self.assertEqual(Path(calls[0][1]), _bridge_script().parent)

    def test_missing_node_gives_a_clear_error(self) -> None:
        session = whatsapp_session(self.data)
        with patch("flow.whatsapp.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                invoke(session, "pair", {})
        self.assertIn("precisa do Node", str(caught.exception))


class PackagingFilesTest(unittest.TestCase):
    def test_spec_ships_node_and_bridge_packages(self) -> None:
        spec = (_ROOT / "flow.spec").read_text(encoding="utf-8")
        self.assertIn('"flow/baileys/node_modules"', spec)
        self.assertIn("node-runtime", spec)
        self.assertIn('"flow/node" if windows else "flow/node/bin"', spec)
        self.assertIn("raise SystemExit", spec)

    def test_scripts_use_the_lockfile(self) -> None:
        up = (_ROOT / "up.sh").read_text(encoding="utf-8")
        build = (_ROOT / "scripts" / "build_app.sh").read_text(encoding="utf-8")
        self.assertIn("npm ci", up)
        self.assertNotIn("npm install", up)
        self.assertIn("npm ci", build)
        self.assertLess(build.index("npm ci"), build.index("pyinstaller"))
        self.assertTrue(os.access(_ROOT / "scripts" / "build_app.sh", os.X_OK))

    def test_node_install_refuses_relative_target(self) -> None:
        completed = subprocess.run(
            ["bash", str(_ROOT / "node-install.sh"), "pasta-relativa"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "Este script é para macOS" in completed.stdout:
            self.skipTest("node-install.sh só roda no macOS")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("caminho absoluto", completed.stdout)


if __name__ == "__main__":
    unittest.main()
