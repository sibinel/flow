"""Leitura sobreposta do WhatsApp e confirmação monotônica de cursor."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import whatsapp_session
from flow.store import open_database
from flow.whatsapp import WhatsAppReader, commit_cursor, discard_cursor


def _session(root: Path) -> Path:
    session = whatsapp_session(root)
    session.mkdir(parents=True)
    (session / "creds.json").write_text('{"registered":true}', encoding="utf-8")
    return session


def _message(identifier: str, chat: str, time: str, note: str = "") -> dict:
    return {
        "id": identifier, "from": chat, "received_at": time,
        "text": identifier, "attachments": [], "media_note": note,
    }


class OverlapTest(unittest.TestCase):
    def test_overlap_late_messages_same_id_in_two_chats_and_monotonic_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session = _session(root)
            db = open_database(root)
            chat = "+5511987654321"
            other = "+5511987654322"
            initial = {chat: {"id": "new", "time": "2026-11-22T11:00:00+00:00"}}
            (session / "cursor.json").write_text(json.dumps(initial), encoding="utf-8")
            messages = [
                _message("same", chat, "2026-11-22T10:59:30+00:00"),
                _message("same", other, "2026-11-22T11:00:30+00:00"),
            ]
            payloads = []

            def runner(_session, _operation, payload):
                payloads.append(payload)
                return {"messages": messages}

            result = WhatsAppReader(db, root, runner, overlap_seconds=60).ingest()
            self.assertEqual(len(result.saved), 2)
            self.assertEqual(payloads[0]["overlap_seconds"], 60)
            self.assertEqual(json.loads((session / "cursor.json").read_text()), initial)
            db.commit()
            commit_cursor(db)
            cursor = json.loads((session / "cursor.json").read_text())
            self.assertEqual(cursor[chat], initial[chat])
            self.assertEqual(cursor[other]["id"], "same")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0], 2)
            db.close()

    def test_media_and_history_warnings_do_not_hold_slot_or_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            session = _session(root)
            messages = [
                _message("a", "+5511987654321", "2026-11-22T10:00:00+00:00", "mídia indisponível: limite de tamanho"),
                _message("b", "+5511987654321", "2026-11-22T10:01:00+00:00"),
            ]
            response = {"messages": messages, "warnings": ["histórico indisponível"]}
            with patch("flow.whatsapp.invoke", return_value=response):
                self.assertTrue(controller.run_cycle(datetime(2026, 11, 23, 8, 1)).ran)
                self.assertFalse(controller.run_cycle(datetime(2026, 11, 23, 8, 2)).ran)
            cursor = json.loads((session / "cursor.json").read_text())
            self.assertEqual(cursor["+5511987654321"]["id"], "b")
            self.assertIn("histórico", controller.notice.lower())
            self.assertIn("mídia", controller.notice.lower())
            controller.close()

    def test_failed_commit_never_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session = _session(root)
            db = open_database(root)
            messages = [_message("a", "+5511987654321", "2026-11-22T10:00:00+00:00")]
            WhatsAppReader(db, root, lambda *_: {"messages": messages}).ingest()
            with self.assertRaises(RuntimeError):
                commit_cursor(db)
            self.assertFalse((session / "cursor.json").exists())
            db.rollback()
            discard_cursor(db)
            db.close()

    def test_commit_error_then_rollback_does_not_confirm_unstored_message(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            session = _session(root)
            db = open_database(root)
            messages = [_message("a", "+5511987654321", "2026-11-22T10:00:00+00:00")]
            WhatsAppReader(db, root, lambda *_: {"messages": messages}).ingest()
            with patch.object(type(db), "commit", side_effect=sqlite3.OperationalError("falhou")):
                with self.assertRaises(sqlite3.OperationalError):
                    db.commit()
            db.rollback()
            commit_cursor(db)
            cursor = json.loads((session / "cursor.json").read_text())
            self.assertNotIn("+5511987654321", cursor)
            db.close()
