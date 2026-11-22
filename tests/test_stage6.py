"""Etapa 6: SMTP só depois da aprovação. O classificador não vê a tool."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from flow.classify import Classifier, ReadTool
from flow.controller import Controller
from flow.paths import package_dir
from flow.send import SmtpSender
from flow.store import open_database


class FakeSmtp:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.commands: list[str] = []
        self._socket = socket.socket()
        self._socket.bind(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        self._socket.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._socket.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        try:
            conn, _addr = self._socket.accept()
        except OSError:
            return
        with conn:
            file = conn.makefile("rwb")
            file.write(b"220 fake\r\n")
            file.flush()
            while True:
                line = file.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                command = text.split(" ", 1)[0].upper()
                self.commands.append(command)
                if command == "DATA":
                    file.write(b"354 go\r\n")
                    file.flush()
                    body: list[bytes] = []
                    while True:
                        chunk = file.readline()
                        if chunk in {b".\r\n", b".\n"}:
                            break
                        body.append(chunk)
                    self.messages.append(b"".join(body))
                    file.write(b"250 queued\r\n")
                elif command == "QUIT":
                    file.write(b"221 bye\r\n")
                    file.flush()
                    break
                else:
                    file.write(b"250 ok\r\n")
                file.flush()


class SmtpSenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_only_an_approved_draft_is_sent(self) -> None:
        client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        contact = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
            (client,),
        ).lastrowid
        entry = self.db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, recebido_em, texto, estado
            ) VALUES (?, ?, '2026-11-22T10:00:00', 'contrato', 'nova')
            """,
            (client, contact),
        ).lastrowid
        draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Ana, o contrato chegou. Confirmo o recebimento?', 'aguardando')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()

        server = FakeSmtp()
        try:
            held = SmtpSender(self.db)
            self.assertEqual(held.tool_names(), ())
            self.assertFalse(held.send(draft, "127.0.0.1", server.port, "escritorio@flow.local"))
            self.assertEqual(server.commands, [])
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

            self.assertTrue(self.controller.approve(draft))
            self.assertEqual(Classifier(ReadTool(self.db)).tool_names(), ("leitura",))
            self.assertNotIn("envio", Classifier(ReadTool(self.db)).tool_names())

            self.assertTrue(
                self.controller.send_approved_mail(
                    draft, "127.0.0.1", server.port, "escritorio@flow.local"
                )
            )
        finally:
            server.close()

        saved = self.db.execute(
            "SELECT enviado_em, canal FROM envio WHERE rascunho_id = ?",
            (draft,),
        ).fetchone()
        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?",
            (draft,),
        ).fetchone()["estado"]
        self.assertEqual(saved["canal"], "email")
        self.assertTrue(saved["enviado_em"])
        self.assertEqual(state, "enviado")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
        self.assertTrue(server.messages)
        self.assertIn(
            "Confirmo o recebimento?",
            b"".join(server.messages).decode("utf-8", "replace"),
        )

    def test_window_does_not_send(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("smtp", "send_approved"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
