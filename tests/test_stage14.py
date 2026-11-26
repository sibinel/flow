"""Etapa 14: o horário entra no fuso da máquina. Data ilegível vira pendência."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flow.controller import Controller
from flow.incoming import open_entry
from flow.store import open_database
from flow.when import parse_received

SP = ZoneInfo("America/Sao_Paulo")


class ReceivedTimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)
        self.client = self.db.execute(
            "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
        ).lastrowid
        self.contact = self.db.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
            (self.client, "+5511987654321"),
        ).lastrowid
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _nova(self, when: str, text: str) -> int:
        return self.db.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, processo_id, recebido_em, texto, estado
            ) VALUES (NULL, ?, NULL, ?, ?, 'nova')
            """,
            (self.contact, when, text),
        ).lastrowid

    def test_sao_paulo_morning_keeps_the_later_message_out(self) -> None:
        early = self._nova("2026-11-23T07:30:00-03:00", "Bruno, chegou cedo.")
        late = self._nova("2026-11-23T09:30:00-03:00", "Bruno, chegou depois.")
        utc = open_entry(
            self.db,
            kind="whatsapp",
            value="+5511987654321",
            message_id="wa-manha",
            received_at="2026-11-23T10:30:00.000Z",
            text="Bruno, isto é sete e meia em São Paulo.",
            has_text=True,
            has_file=False,
            account="whatsapp",
        )
        broken = open_entry(
            self.db,
            kind="whatsapp",
            value="+5511987654321",
            message_id="wa-sem-data",
            received_at="nao-e-data",
            text="Sem data legível.",
            has_text=True,
            has_file=False,
            account="whatsapp",
        )
        leftover = self._nova("quarta-feira", "Data que o ciclo ainda vai segurar.")
        self.db.commit()

        stored = self.db.execute(
            "SELECT recebido_em FROM entrada WHERE id = ?",
            (utc,),
        ).fetchone()["recebido_em"]
        self.assertEqual(
            parse_received(stored),
            parse_received("2026-11-23T07:30:00-03:00"),
        )

        morning = datetime(2026, 11, 23, 8, 0, tzinfo=SP)
        self.controller.run_cycle(morning, lambda draft_id, channel: False)

        early_state = self.db.execute(
            "SELECT estado FROM entrada WHERE id = ?",
            (early,),
        ).fetchone()["estado"]
        late_state = self.db.execute(
            "SELECT estado FROM entrada WHERE id = ?",
            (late,),
        ).fetchone()["estado"]
        utc_state = self.db.execute(
            "SELECT estado FROM entrada WHERE id = ?",
            (utc,),
        ).fetchone()["estado"]
        self.assertNotEqual(early_state, "nova")
        self.assertEqual(late_state, "nova")
        self.assertNotEqual(utc_state, "nova")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

        for entry_id in (broken, leftover):
            row = self.db.execute(
                "SELECT estado FROM entrada WHERE id = ?",
                (entry_id,),
            ).fetchone()
            reason = self.db.execute(
                "SELECT motivo FROM pendencia WHERE entrada_id = ?",
                (entry_id,),
            ).fetchone()
            self.assertEqual(row["estado"], "pendente")
            self.assertEqual(reason["motivo"], "data ilegível")
            self.assertIsNone(
                self.db.execute(
                    "SELECT 1 FROM rascunho WHERE entrada_id = ?",
                    (entry_id,),
                ).fetchone()
            )
