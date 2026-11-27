"""Etapa 25: o cursor anda depois do banco, e o e-mail não confunde a conta."""

from __future__ import annotations

import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database
from flow.whatsapp import WhatsAppReader, discard_cursor


def _mail(sender: str, message_id: str) -> bytes:
    mail = EmailMessage()
    mail["From"] = sender
    mail["To"] = "escritorio@flow.local"
    mail["Date"] = "Wed, 18 Nov 2026 10:00:00 -0300"
    mail["Message-ID"] = message_id
    mail.set_content("O mesmo aviso.")
    return mail.as_bytes()


class CursorAndAccountTest(unittest.TestCase):
    def _session(self, root: Path) -> Path:
        session = whatsapp_session(root)
        session.mkdir(parents=True)
        (session / "creds.json").write_text(
            json.dumps({"registered": True}),
            encoding="utf-8",
        )
        return session

    def test_the_cursor_waits_for_the_commit_and_stops_at_the_gap(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            session = self._session(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (client,),
            )
            db.commit()
            messages = [
                {
                    "id": "a",
                    "from": "+5511987654321",
                    "text": "um",
                    "received_at": "2026-11-22T11:00:00-03:00",
                    "attachments": [],
                },
                {
                    "id": "b",
                    "from": "+5511987654321",
                    "text": "dois",
                    "received_at": "2026-11-22T11:01:00-03:00",
                    "attachments": [],
                },
                {
                    "id": "c",
                    "from": "+5511987654321",
                    "text": "três",
                    "received_at": "2026-11-22T11:02:00-03:00",
                    "attachments": [],
                },
            ]

            def runner(_folder, op, _payload):
                self.assertEqual(op, "read")
                return {"messages": messages}

            reader = WhatsAppReader(db, root, runner)
            reader.ingest()
            self.assertFalse((session / "cursor.json").is_file())
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 3)
            discard_cursor(db)
            db.close()
            controller.close()

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            session = self._session(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (client,),
            )
            db.commit()
            db.close()
            original = WhatsAppReader._save
            during: list[bool] = []

            def flaky(reader, message):
                during.append((session / "cursor.json").is_file())
                if message.get("id") == "b":
                    raise RuntimeError("meio")
                return original(reader, message)

            with patch.object(WhatsAppReader, "_save", flaky):
                ids = controller.ingest_whatsapp(runner)
            self.assertEqual(len(ids), 2)
            self.assertEqual(during, [False, False, False])
            db = open_database(root)
            stored = db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
            db.close()
            self.assertEqual(stored, 2)
            mark = json.loads((session / "cursor.json").read_text(encoding="utf-8"))
            self.assertEqual(
                mark["+5511987654321"],
                {"id": "a", "time": "2026-11-22T11:00:00-03:00"},
            )
            bridge = (package_dir() / "baileys" / "bridge.mjs").read_text(encoding="utf-8")
            self.assertNotIn("saveCursor", bridge)
            self.assertIn("cursor.json", bridge)
            db.close()
            controller.close()

    def test_new_contact_with_failed_media_keeps_both_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            self._session(root)

            def runner(_folder, op, _payload):
                return {
                    "messages": [
                        {
                            "id": "mid-novo",
                            "from": "+5511900009999",
                            "text": "",
                            "received_at": "2026-11-22T13:00:00-03:00",
                            "attachments": [],
                            "media_note": "mídia não baixada: áudio",
                        }
                    ]
                }

            ids = controller.ingest_whatsapp(runner)
            self.assertEqual(len(ids), 1)
            db = open_database(root)
            reasons = [
                row["motivo"]
                for row in db.execute(
                    "SELECT motivo FROM pendencia WHERE entrada_id = ? ORDER BY id",
                    (ids[0],),
                )
            ]
            db.close()
            self.assertEqual(reasons, ["contato novo", "mídia não baixada"])
            controller.close()

    def test_the_same_message_id_in_another_mailbox_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana@email.com')",
                (client,),
            ).lastrowid
            db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado,
                    mensagem_id, canal, conta
                ) VALUES (?, ?, '2026-11-18T10:00:00', 'O mesmo aviso.', 'nova', ?, 'email', ?)
                """,
                (client, contact, "<mesmo@flow>", "ana@email.com"),
            )
            db.commit()
            db.close()
            raw = _mail("Ana Lima <ana@email.com>", "<mesmo@flow>")
            with patch("flow.mail._fetch", return_value=[raw]):
                created = controller.ingest_mail(
                    "127.0.0.1", 143, "secretaria@escritorio.com", "segredo"
                )
            self.assertEqual(len(created), 1)
            with patch("flow.mail._fetch", return_value=[raw]):
                again = controller.ingest_mail(
                    "127.0.0.1", 143, "secretaria@escritorio.com", "segredo"
                )
            self.assertEqual(again, [])
            db = open_database(root)
            accounts = [
                row["conta"]
                for row in db.execute(
                    """
                    SELECT conta FROM entrada
                    WHERE mensagem_id = '<mesmo@flow>'
                    ORDER BY id
                    """
                )
            ]
            db.close()
            self.assertEqual(accounts, ["ana@email.com", "secretaria@escritorio.com"])
            controller.close()
