"""Etapa 15: o mesmo id em outra conta ou em outro chat continua sendo outra mensagem."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.incoming import open_entry, scoped_message_id
from flow.paths import whatsapp_session
from flow.store import database_file, open_database


class ScopedMessageIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_same_id_in_two_mailboxes_and_two_chats_is_kept(self) -> None:
        self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
        )
        self.db.commit()
        first = open_entry(
            self.db,
            kind="email",
            value="ana.lima@email.com",
            message_id="<mesmo@flow>",
            received_at="2026-11-23T08:00:00-03:00",
            text="Caixa da Ana.",
            has_text=True,
            has_file=False,
            account="ana@escritorio.com",
        )
        second = open_entry(
            self.db,
            kind="email",
            value="ana.lima@email.com",
            message_id="<mesmo@flow>",
            received_at="2026-11-23T08:00:00-03:00",
            text="Caixa do escritório.",
            has_text=True,
            has_file=False,
            account="escritorio@flow.com",
        )
        repeated = open_entry(
            self.db,
            kind="email",
            value="ana.lima@email.com",
            message_id="<mesmo@flow>",
            received_at="2026-11-23T08:00:00-03:00",
            text="Caixa da Ana de novo.",
            has_text=True,
            has_file=False,
            account="ana@escritorio.com",
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(repeated)

        chat_a = open_entry(
            self.db,
            kind="whatsapp",
            value="+5511987654321",
            message_id="abc",
            received_at="2026-11-23T08:10:00-03:00",
            text="Chat do Bruno.",
            has_text=True,
            has_file=False,
            account="whatsapp",
        )
        chat_b = open_entry(
            self.db,
            kind="whatsapp",
            value="+5511900001111",
            message_id="abc",
            received_at="2026-11-23T08:11:00-03:00",
            text="Outro chat.",
            has_text=True,
            has_file=False,
            account="whatsapp",
        )
        self.assertIsNotNone(chat_a)
        self.assertIsNotNone(chat_b)
        self.assertNotEqual(
            scoped_message_id("whatsapp", "+5511987654321", "abc"),
            scoped_message_id("whatsapp", "+5511900001111", "abc"),
        )

        blank_one = open_entry(
            self.db,
            kind="email",
            value="ana.lima@email.com",
            message_id="",
            received_at="2026-11-23T08:20:00-03:00",
            text="Sem id, primeira.",
            has_text=True,
            has_file=False,
            account="ana@escritorio.com",
        )
        blank_two = open_entry(
            self.db,
            kind="email",
            value="ana.lima@email.com",
            message_id="",
            received_at="2026-11-23T08:21:00-03:00",
            text="Sem id, segunda.",
            has_text=True,
            has_file=False,
            account="ana@escritorio.com",
        )
        self.assertIsNotNone(blank_one)
        self.assertIsNotNone(blank_two)
        self.db.commit()
        stored = self.db.execute("SELECT COUNT(*) FROM entrada").fetchone()[0]
        self.assertEqual(stored, 6)

    def test_old_rows_gain_channel_and_account_without_losing_text(self) -> None:
        self.db.close()
        self.controller.close()
        path = database_file(Path(self.tmp.name))
        path.unlink()
        with sqlite3.connect(path) as planted:
            planted.executescript(
                """
                CREATE TABLE cliente (
                    id INTEGER PRIMARY KEY,
                    nome TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE contato (
                    id INTEGER PRIMARY KEY,
                    cliente_id INTEGER NOT NULL,
                    tipo TEXT NOT NULL,
                    valor TEXT NOT NULL
                );
                CREATE TABLE entrada (
                    id INTEGER PRIMARY KEY,
                    cliente_id INTEGER,
                    contato_id INTEGER NOT NULL,
                    processo_id INTEGER,
                    recebido_em TEXT NOT NULL,
                    texto TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    mensagem_id TEXT
                );
                """
            )
            planted.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
            )
            planted.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (1, 'whatsapp', '+5511987654321')
                """
            )
            planted.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, processo_id, recebido_em, texto, estado, mensagem_id
                ) VALUES (1, 1, NULL, '2026-11-18T10:00:00', 'texto antigo', 'nova', 'abc')
                """
            )
            planted.commit()

        with open_database(Path(self.tmp.name)) as again:
            row = again.execute(
                "SELECT texto, canal, conta, mensagem_id FROM entrada WHERE id = 1"
            ).fetchone()
            self.assertEqual(row["texto"], "texto antigo")
            self.assertEqual(row["canal"], "whatsapp")
            self.assertEqual(row["conta"], "whatsapp")
            self.assertEqual(
                row["mensagem_id"],
                scoped_message_id("whatsapp", "+5511987654321", "abc"),
            )


class WhatsAppReadUsesTheChatTest(unittest.TestCase):
    def test_reader_keeps_two_chats_with_the_same_id(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            try:
                session = whatsapp_session(Path(name))
                session.mkdir()
                (session / "creds.json").write_text(
                    json.dumps({"registered": True}),
                    encoding="utf-8",
                )

                def runner(folder: Path, op: str, payload: dict) -> dict:
                    return {
                        "messages": [
                            {
                                "id": "abc",
                                "from": "+5511987654321",
                                "text": "um",
                                "received_at": "2026-11-23T08:00:00-03:00",
                                "attachments": [],
                            },
                            {
                                "id": "abc",
                                "from": "+5511900001111",
                                "text": "dois",
                                "received_at": "2026-11-23T08:01:00-03:00",
                                "attachments": [],
                            },
                        ]
                    }

                saved = controller.ingest_whatsapp(runner)
                self.assertEqual(len(saved), 2)
            finally:
                controller.close()
