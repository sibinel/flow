"""Etapa 22: anexo que falha não fica no disco, e uma parte só vira documento."""

from __future__ import annotations

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import store_document
from flow.store import open_database


def _text(message_id: str, body: str) -> bytes:
    mail = EmailMessage()
    mail["From"] = "Ana Lima <ana.lima@email.com>"
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail["Message-ID"] = message_id
    mail.set_content(body)
    return mail.as_bytes()


def _two_files() -> bytes:
    mail = EmailMessage()
    mail["From"] = "Ana Lima <ana.lima@email.com>"
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:05:00 -0300"
    mail["Message-ID"] = "<dois@flow>"
    mail.set_content("Segue os dois.")
    mail.add_attachment(b"um", maintype="application", subtype="pdf", filename="um.pdf")
    mail.add_attachment(b"dois", maintype="application", subtype="pdf", filename="dois.pdf")
    return mail.as_bytes()


def _single_file() -> bytes:
    mail = EmailMessage()
    mail["From"] = "Ana Lima <ana.lima@email.com>"
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:10:00 -0300"
    mail["Message-ID"] = "<unico@flow>"
    mail.set_content(
        b"%PDF-1.4 unico",
        maintype="application",
        subtype="pdf",
        filename="unico.pdf",
        disposition="attachment",
    )
    return mail.as_bytes()


class AttachmentFailureTest(unittest.TestCase):
    def test_a_later_failure_removes_the_files_of_that_message(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            )
            db.commit()
            db.close()
            calls = {"n": 0}

            def flaky(connection, docs, display_name, entry_id, index, payload):
                calls["n"] += 1
                stored = store_document(
                    connection, docs, display_name, entry_id, index, payload
                )
                if calls["n"] == 2:
                    raise RuntimeError("segunda linha")
                return stored

            with (
                patch("flow.mail._fetch", return_value=[_text("<ok@flow>", "só texto"), _two_files()]),
                patch("flow.mail.store_document", flaky),
            ):
                ids = controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")
            self.assertEqual(len(ids), 1)
            db = open_database(root)
            stored = db.execute("SELECT mensagem_id, texto FROM entrada").fetchall()
            self.assertEqual([row["mensagem_id"] for row in stored], ["<ok@flow>"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documento").fetchone()[0], 0)
            db.close()
            docs = root / "documentos"
            left = [item.name for item in docs.iterdir()] if docs.is_dir() else []
            self.assertEqual(left, [])
            controller.close()

    def test_a_single_attachment_part_becomes_a_document(self) -> None:
        raw = _single_file()
        self.assertNotIn(b"multipart", raw.split(b"\r\n\r\n", 1)[0].lower())
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            )
            db.commit()
            db.close()
            with patch("flow.mail._fetch", return_value=[raw]):
                ids = controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")
            self.assertEqual(len(ids), 1)
            db = open_database(root)
            entry = db.execute(
                "SELECT texto, estado FROM entrada WHERE id = ?",
                (ids[0],),
            ).fetchone()
            document = db.execute(
                "SELECT nome, caminho FROM documento WHERE entrada_id = ?",
                (ids[0],),
            ).fetchone()
            self.assertEqual(entry["texto"], "")
            self.assertEqual(entry["estado"], "nova")
            self.assertEqual(document["nome"], "unico.pdf")
            self.assertEqual(Path(document["caminho"]).read_bytes(), b"%PDF-1.4 unico")
            db.close()
            controller.close()
