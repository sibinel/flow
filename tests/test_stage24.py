"""Etapa 24: a coleta é separada e a vaga só fecha quando ela termina."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import whatsapp_session
from flow.store import open_database


def _model(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class SeparateCollectTest(unittest.TestCase):
    def test_imap_failure_still_reads_whatsapp_and_the_slot_stays_open(self) -> None:
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
            session = whatsapp_session(root)
            session.mkdir(parents=True)
            (session / "creds.json").write_text(
                json.dumps({"registered": True}),
                encoding="utf-8",
            )
            calls: list[str] = []

            def imap_down(*_args, **_kwargs):
                calls.append("imap")
                raise RuntimeError("imap caiu")

            def whatsapp(_folder, op, _payload):
                calls.append(op)
                return {"messages": []}

            with (
                patch("flow.controller.ImapReader.ingest", imap_down),
                patch("flow.whatsapp.invoke", whatsapp),
            ):
                first = controller.run_cycle(datetime(2026, 11, 23, 8, 1), lambda *_args: False)
                second = controller.run_cycle(datetime(2026, 11, 23, 8, 2), lambda *_args: False)
            config = json.loads((root / "ciclo.json").read_text(encoding="utf-8"))
            self.assertTrue(first.ran)
            self.assertTrue(second.ran)
            self.assertEqual(calls, ["imap", "read", "imap", "read"])
            self.assertNotIn("ultimo", config)
            controller.close()

    def test_model_failure_returns_the_entry_and_the_next_day_tries_again(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            bruno = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
            ).lastrowid
            phone = db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (bruno,),
            ).lastrowid
            db.execute(
                'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
                (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
            )
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (NULL, ?, '2026-11-23T07:00:00', ?, 'nova')
                """,
                (phone, "Bruno, segue a petição da rescisão."),
            ).lastrowid
            db.execute(
                """
                INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
                VALUES (?, ?, 'whatsapp', '2026-11-01T09:00:00', ?)
                """,
                (bruno, entry, "Bruno segue a petição da rescisão"),
            )
            db.commit()
            db.close()

            def down(_url, _body, _headers):
                raise RuntimeError("modelo caiu")

            with patch("flow.llm.post_chat", down):
                failed = controller.run_cycle(datetime(2026, 11, 23, 8, 0), lambda *_args: False)
            db = open_database(root)
            state = db.execute(
                "SELECT estado FROM entrada WHERE id = ?",
                (entry,),
            ).fetchone()["estado"]
            draft = db.execute(
                "SELECT 1 FROM rascunho WHERE entrada_id = ?",
                (entry,),
            ).fetchone()
            db.close()
            self.assertTrue(failed.ran)
            self.assertEqual(state, "nova")
            self.assertIsNone(draft)

            with patch("flow.llm.post_chat", return_value=_model("Pode protocolar.")):
                again = controller.run_cycle(datetime(2026, 11, 24, 8, 0), lambda *_args: False)
            db = open_database(root)
            row = db.execute(
                "SELECT estado, texto FROM rascunho WHERE entrada_id = ?",
                (entry,),
            ).fetchone()
            filed = db.execute(
                "SELECT estado FROM entrada WHERE id = ?",
                (entry,),
            ).fetchone()["estado"]
            db.close()
            self.assertTrue(again.ran)
            self.assertEqual(filed, "arquivada")
            self.assertEqual(row["estado"], "aguardando")
            self.assertEqual(row["texto"], "Pode protocolar.")
            controller.close()

    def test_an_empty_model_reply_stays_archived(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            bruno = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
            ).lastrowid
            phone = db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (bruno,),
            ).lastrowid
            db.execute(
                'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
                (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
            )
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (NULL, ?, '2026-11-23T07:00:00', ?, 'nova')
                """,
                (phone, "Bruno, segue a petição da rescisão."),
            ).lastrowid
            db.execute(
                """
                INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
                VALUES (?, ?, 'whatsapp', '2026-11-01T09:00:00', ?)
                """,
                (bruno, entry, "Bruno segue a petição da rescisão"),
            )
            db.commit()
            db.close()
            calls = {"n": 0}

            def empty(_url, _body, _headers):
                calls["n"] += 1
                return _model("   ")

            with patch("flow.llm.post_chat", empty):
                controller.run_cycle(datetime(2026, 11, 23, 8, 0), lambda *_args: False)
                controller.run_cycle(datetime(2026, 11, 24, 8, 0), lambda *_args: False)
            db = open_database(root)
            state = db.execute(
                "SELECT estado FROM entrada WHERE id = ?",
                (entry,),
            ).fetchone()["estado"]
            draft = db.execute(
                "SELECT 1 FROM rascunho WHERE entrada_id = ?",
                (entry,),
            ).fetchone()
            db.close()
            self.assertEqual(calls["n"], 1)
            self.assertEqual(state, "arquivada")
            self.assertIsNone(draft)
            controller.close()
