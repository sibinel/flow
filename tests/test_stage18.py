"""Etapa 18: coleta e envio usam servidores diferentes."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.mail import load_mail_account
from flow.send import load_smtp_target
from flow.store import open_database


class SplitMailAccountTest(unittest.TestCase):
    def test_old_file_collects_and_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            (root / "email.json").write_text(
                json.dumps(
                    {
                        "host": "imap.example.com",
                        "port": 993,
                        "user": "ana@example.com",
                        "password": "segredo",
                    }
                ),
                encoding="utf-8",
            )
            account = load_mail_account(root)
            self.assertIsNotNone(account)
            assert account is not None
            self.assertEqual((account.imap_host, account.imap_port), ("imap.example.com", 993))
            self.assertIsNone(load_smtp_target(root))

            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            ).lastrowid
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'contrato', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, confirmo.', 'aprovado')",
                (entry,),
            ).lastrowid
            db.commit()
            db.close()

            seen: list[tuple] = []

            def collect(self, host, port, user, password):
                seen.append((host, port, user, password))
                return []

            with (
                patch("flow.controller.ImapReader.ingest", collect),
                patch("flow.send.smtplib.SMTP_SSL", side_effect=AssertionError("enviou")),
                patch("flow.send.smtplib.SMTP", side_effect=AssertionError("enviou")),
            ):
                opened = controller._tick(datetime(2026, 11, 23, 8, 0))

            self.assertTrue(opened.ran)
            self.assertEqual(seen, [("imap.example.com", 993, "ana@example.com", "segredo")])
            self.assertEqual(opened.sent, ())
            state = open_database(root)
            self.assertEqual(
                state.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "aprovado",
            )
            self.assertEqual(state.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            state.close()
            controller.close()

    def test_split_file_sends_through_the_other_server(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            (root / "email.json").write_text(
                json.dumps(
                    {
                        "imap_host": "imap.example.com",
                        "imap_port": 993,
                        "smtp_host": "smtp.example.com",
                        "smtp_port": 465,
                        "user": "ana@example.com",
                        "password": "segredo",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_smtp_target(root), ("smtp.example.com", 465))
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            ).lastrowid
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'contrato', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, confirmo.', 'aprovado')",
                (entry,),
            ).lastrowid
            db.commit()
            db.close()

            seen: list[tuple] = []
            sent_to: list[tuple] = []

            def collect(self, host, port, user, password):
                seen.append((host, port))
                return []

            class QuietSmtp:
                def __init__(self, host, port, timeout=None, context=None) -> None:
                    sent_to.append((host, port))

                def __enter__(self):
                    return self

                def __exit__(self, *args) -> bool:
                    return False

                def login(self, user, password) -> None:
                    return None

                def send_message(self, message) -> None:
                    return None

            with (
                patch("flow.controller.ImapReader.ingest", collect),
                patch("flow.send.smtplib.SMTP_SSL", new=QuietSmtp),
            ):
                opened = controller._tick(datetime(2026, 11, 23, 8, 0))

            self.assertEqual(seen, [("imap.example.com", 993)])
            self.assertEqual(sent_to, [("smtp.example.com", 465)])
            self.assertEqual(opened.sent, (draft,))
            controller.close()
