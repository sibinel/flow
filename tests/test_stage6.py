"""Etapa 6: SMTP só depois da aprovação. O classificador não vê a tool."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import patch
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
            self.assertFalse(
                held.send(draft, "127.0.0.1", server.port, "escritorio@flow.local", "segredo")
            )
            self.assertEqual(server.commands, [])
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

            self.assertTrue(self.controller.approve(draft))
            self.assertEqual(Classifier(ReadTool(self.db)).tool_names(), ("leitura",))
            self.assertNotIn("envio", Classifier(ReadTool(self.db)).tool_names())

            with patch("flow.send.smtplib.SMTP_SSL", side_effect=AssertionError("ssl")):
                self.assertTrue(
                    self.controller.send_approved_mail(
                        draft, "127.0.0.1", server.port, "escritorio@flow.local", "segredo"
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
        self.assertIsNone(saved)
        self.assertEqual(state, "aprovado")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertTrue(server.messages)
        self.assertIn(
            "Confirmo o recebimento?",
            b"".join(server.messages).decode("utf-8", "replace"),
        )
        self.assertNotIn("AUTH", server.commands)

    def test_remote_smtp_logs_in_after_tls_and_keeps_the_port(self) -> None:
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
            VALUES (?, 'Ana, confirmo.', 'aguardando')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()
        self.assertTrue(self.controller.approve(draft))
        trace: list[tuple] = []

        class SecureSmtp:
            def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
                trace.append(("connect", host, port))

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

            def login(self, user: str, password: str) -> None:
                trace.append(("login", user, password))

            def send_message(self, message) -> None:
                trace.append(("data", str(message["From"]), str(message["To"])))

        with (
            patch("flow.send.smtplib.SMTP", side_effect=AssertionError("claro")),
            patch("flow.send.smtplib.SMTP_SSL", new=SecureSmtp),
        ):
            self.assertTrue(
                self.controller.send_approved_mail(
                    draft, "smtp.example", 2525, "escritorio@flow.local", "segredo"
                )
            )

        self.assertEqual(
            trace,
            [
                ("connect", "smtp.example", 2525),
                ("login", "escritorio@flow.local", "segredo"),
                ("data", "escritorio@flow.local", "ana.lima@email.com"),
            ],
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
            "aprovado",
        )

    def test_cycle_and_sender_record_one_row(self) -> None:
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
            ) VALUES (?, ?, '2026-11-22T09:00:00', 'contrato', 'arquivada')
            """,
            (client, contact),
        ).lastrowid
        draft = self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Ana, confirmo.', 'aprovado')
            """,
            (entry,),
        ).lastrowid
        self.db.commit()
        server = FakeSmtp()
        sender = SmtpSender(self.controller._db)

        def dispatch(draft_id: int, channel: str) -> bool:
            self.assertEqual(channel, "email")
            return sender.send(
                draft_id, "127.0.0.1", server.port, "escritorio@flow.local", "segredo"
            )

        try:
            sent = self.controller.run_cycle(datetime(2026, 11, 23, 8, 0), dispatch)
            self.assertEqual(sent.sent, (draft,))
            self.assertFalse(
                sender.send(draft, "127.0.0.1", server.port, "escritorio@flow.local", "segredo")
            )
            again = self.controller.run_cycle(datetime(2026, 11, 24, 8, 0), dispatch)
        finally:
            server.close()

        self.assertEqual(again.sent, ())
        self.assertEqual(len(server.messages), 1)
        rows = self.db.execute(
            "SELECT canal FROM envio WHERE rascunho_id = ?",
            (draft,),
        ).fetchall()
        self.assertEqual([row["canal"] for row in rows], ["email"])
        self.assertEqual(
            self.db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
            "enviado",
        )

    def test_window_does_not_send(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("smtp", "send_approved"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
