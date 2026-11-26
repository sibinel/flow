"""Etapa 5: IMAP só grava entrada e documento. Nada sai."""

from __future__ import annotations

import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from email.message import EmailMessage
from pathlib import Path

from flow.controller import Controller
from flow.mail import ImapReader
from flow.paths import document_target, package_dir
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
            with patch("flow.mail.imaplib.IMAP4_SSL", side_effect=AssertionError("ssl")):
                entry_ids = self.controller.ingest_mail(
                    "127.0.0.1", server.port, "ana", "segredo"
                )
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

    def test_remote_imap_is_born_encrypted_and_keeps_the_port(self) -> None:
        trace: list[tuple] = []

        class SecureImap:
            def __init__(self, host: str, port: int, *, ssl_context=None, timeout=None) -> None:
                trace.append(("connect", host, port, ssl_context))

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

            def login(self, user: str, password: str) -> None:
                trace.append(("login", user, password))

            def select(self, mailbox: str, readonly: bool = False):
                trace.append(("select", mailbox))
                return "OK", [b"0"]

            def search(self, charset, criteria):
                return "OK", [b""]

        with (
            patch("flow.mail.imaplib.IMAP4", side_effect=AssertionError("claro")),
            patch("flow.mail.imaplib.IMAP4_SSL", new=SecureImap),
        ):
            entry_ids = self.controller.ingest_mail("imap.example", 1993, "ana", "segredo")

        self.assertEqual(entry_ids, [])
        context = trace[0][3]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(
            [(item[0], item[1], item[2]) if item[0] == "connect" else item for item in trace],
            [
                ("connect", "imap.example", 1993),
                ("login", "ana", "segredo"),
                ("select", "INBOX"),
            ],
        )

    def test_same_attachment_name_keeps_both_files(self) -> None:
        ana = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        ).lastrowid
        bruno = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
        ).lastrowid
        self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
            (ana,),
        )
        self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'bruno.dias@email.com')",
            (bruno,),
        )
        self.db.commit()
        docs = self.controller.database_path.parent / "documentos"
        target = document_target(docs, "../segredo/contrato.pdf", 3, 1)
        self.assertEqual(target.parent, docs)
        self.assertEqual(target.name, "contrato-3-1.pdf")
        self.assertNotIn("segredo", target.parts)

        first = FakeImap(
            _message_with(
                "Ana Lima <ana.lima@email.com>",
                [
                    ("contrato.pdf", b"contrato da ana"),
                    ("pasta/contrato.pdf", b"segundo contrato da ana"),
                ],
            )
        )
        try:
            ana_ids = self.controller.ingest_mail("127.0.0.1", first.port, "ana", "segredo")
        finally:
            first.close()
        second = FakeImap(
            _message_with(
                "Bruno Dias <bruno.dias@email.com>",
                [("contrato.pdf", b"contrato do bruno")],
            )
        )
        try:
            bruno_ids = self.controller.ingest_mail("127.0.0.1", second.port, "bruno", "segredo")
        finally:
            second.close()

        rows = self.db.execute(
            "SELECT entrada_id, caminho, nome FROM documento ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["nome"] for row in rows], ["contrato.pdf"] * 3)
        self.assertEqual([row["entrada_id"] for row in rows], [ana_ids[0], ana_ids[0], bruno_ids[0]])
        stored = [Path(row["caminho"]) for row in rows]
        self.assertEqual(len({path.resolve() for path in stored}), 3)
        self.assertEqual(
            [path.name for path in stored],
            [
                f"contrato-{ana_ids[0]}-1.pdf",
                f"contrato-{ana_ids[0]}-2.pdf",
                f"contrato-{bruno_ids[0]}-1.pdf",
            ],
        )
        for path in stored:
            self.assertEqual(path.parent, docs)
            self.assertNotIn("pasta", path.parts)
            self.assertNotIn("segredo", path.parts)
        self.assertEqual(stored[0].read_bytes(), b"contrato da ana")
        self.assertEqual(stored[1].read_bytes(), b"segundo contrato da ana")
        self.assertEqual(stored[2].read_bytes(), b"contrato do bruno")
        self.assertFalse((docs / "contrato.pdf").exists())


def _message_with(sender: str, files: list[tuple[str, bytes]]) -> bytes:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail.set_content("Segue o contrato.")
    for name, payload in files:
        mail.add_attachment(
            payload,
            maintype="application",
            subtype="pdf",
            filename=name,
        )
    return mail.as_bytes()
