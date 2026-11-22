"""Etapa 5: IMAP só grava entrada e documento. Nada sai."""

from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from email.message import EmailMessage
from pathlib import Path

from flow.controller import Controller
from flow.mail import ImapReader
from flow.paths import package_dir
from flow.store import open_database


class FakeImap:
    def __init__(self, message: bytes) -> None:
        self._message = message
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
            file.write(b"* OK fake\r\n")
            file.flush()
            while True:
                line = file.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                tag, _, rest = text.partition(" ")
                command = rest.split(" ", 1)[0].upper() if rest else ""
                self.commands.append(command)
                if command == "CAPABILITY":
                    file.write(b"* CAPABILITY IMAP4rev1\r\n")
                elif command in {"SELECT", "EXAMINE"}:
                    file.write(b"* 1 EXISTS\r\n")
                elif command == "SEARCH":
                    file.write(b"* SEARCH 1\r\n")
                elif command == "FETCH":
                    file.write(f"* 1 FETCH (RFC822 {{{len(self._message)}}}\r\n".encode())
                    file.write(self._message)
                    file.write(b")\r\n")
                elif command == "LOGOUT":
                    file.write(f"{tag} OK done\r\n".encode())
                    file.flush()
                    break
                file.write(f"{tag} OK done\r\n".encode())
                file.flush()


def _message() -> bytes:
    mail = EmailMessage()
    mail["From"] = "Ana Lima <ana.lima@email.com>"
    mail["To"] = "escritorio@flow.local"
    mail["Subject"] = "Contrato"
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail.set_content("Segue o contrato assinado.")
    mail.add_attachment(
        b"%PDF-1.4 contrato",
        maintype="application",
        subtype="pdf",
        filename="contrato.pdf",
    )
    return mail.as_bytes()


class ImapReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_message_with_attachment_is_stored_and_nothing_is_sent(self) -> None:
        client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
            (client,),
        )
        before = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        self.db.commit()
        server = FakeImap(_message())
        try:
            entry_ids = self.controller.ingest_mail("127.0.0.1", server.port, "ana", "segredo")
        finally:
            server.close()

        self.assertEqual(len(entry_ids), 1)
        entry = self.db.execute(
            "SELECT cliente_id, contato_id, processo_id, texto, estado FROM entrada WHERE id = ?",
            (entry_ids[0],),
        ).fetchone()
        document = self.db.execute(
            "SELECT caminho, nome FROM documento WHERE entrada_id = ?",
            (entry_ids[0],),
        ).fetchone()
        sent = self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
        after = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        stored = Path(document["caminho"])

        self.assertEqual(entry["texto"], "Segue o contrato assinado.")
        self.assertEqual(entry["estado"], "nova")
        self.assertIsNone(entry["cliente_id"])
        self.assertIsNone(entry["processo_id"])
        self.assertEqual(document["nome"], "contrato.pdf")
        self.assertEqual(stored.read_bytes(), b"%PDF-1.4 contrato")
        self.assertTrue(str(self.controller.database_path.parent) in str(stored))
        self.assertFalse(package_dir().resolve() in stored.resolve().parents)
        self.assertEqual(sent, 0)
        self.assertEqual(after, before)
        self.assertNotIn("APPEND", server.commands)
        self.assertNotIn("SEND", server.commands)

        reader = ImapReader(self.db, self.controller.database_path.parent)
        self.assertEqual(reader.tool_names(), ("leitura",))
        self.assertFalse(hasattr(reader, "send"))

    def test_window_does_not_read_mail(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        mail = (package_dir() / "mail.py").read_text(encoding="utf-8").lower()
        for banned in ("imap", "smtp", "ingest_mail"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
        self.assertNotIn("smtp", mail)
        self.assertNotIn("classify", mail)
