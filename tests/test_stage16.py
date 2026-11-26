"""Etapa 16: se a linha do anexo falha, o arquivo da tentativa é apagado."""

from __future__ import annotations

import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller


def _with_file() -> bytes:
    mail = EmailMessage()
    mail["From"] = "Ana Lima <ana.lima@email.com>"
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail["Message-ID"] = "<anexo@flow>"
    mail.set_content("Segue o contrato.")
    mail.add_attachment(
        b"contrato da ana",
        maintype="application",
        subtype="pdf",
        filename="contrato.pdf",
    )
    return mail.as_bytes()


class OrphanFileTest(unittest.TestCase):
    def test_failed_document_row_removes_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            db = controller._db
            db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            )
            db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (1, 'email', 'ana.lima@email.com')
                """
            )
            db.commit()
            db.execute(
                """
                CREATE TRIGGER documento_recusa BEFORE INSERT ON documento
                BEGIN
                    SELECT RAISE(ABORT, 'linha recusada');
                END
                """
            )
            docs = Path(name) / "documentos"
            try:
                with patch("flow.mail._fetch", return_value=[_with_file()]):
                    saved = controller.ingest_mail("127.0.0.1", 143, "ana", "segredo")
            finally:
                controller.close()

            self.assertEqual(saved, [])
            files = [path for path in docs.rglob("*") if path.is_file()] if docs.exists() else []
            self.assertEqual(files, [])
