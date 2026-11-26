"""Etapa 20: a vaga fica aberta até a próxima e a rede sai da janela."""

from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.cycle import DayCycle
from flow.store import open_database


class OpenSlotTest(unittest.TestCase):
    def test_a_minute_late_uses_the_slot_and_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            db = open_database(Path(name))
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            ).lastrowid
            early = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:30:00', 'chegou cedinho', 'nova')
                """,
                (client, contact),
            ).lastrowid
            late = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T09:30:00', 'chegou depois', 'nova')
                """,
                (client, contact),
            ).lastrowid
            db.commit()
            db.close()

            opened = controller.run_cycle(datetime(2026, 11, 23, 8, 1), lambda *_args: False)
            again = controller.run_cycle(datetime(2026, 11, 23, 10, 0), lambda *_args: True)
            db = open_database(Path(name))
            states = {
                row["id"]: row["estado"]
                for row in db.execute("SELECT id, estado FROM entrada")
            }
            self.assertTrue(opened.ran)
            self.assertFalse(again.ran)
            self.assertNotEqual(states[early], "nova")
            self.assertEqual(states[late], "nova")
            db.close()
            controller.close()

    def test_a_failed_collect_keeps_what_was_already_stored(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
                (client,),
            ).lastrowid
            stored = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'já estava aqui', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, ok.', 'aprovado')",
                (stored,),
            ).lastrowid
            db.commit()

            def collect() -> None:
                db.execute(
                    """
                    INSERT INTO entrada (
                        cliente_id, contato_id, recebido_em, texto, estado
                    ) VALUES (?, ?, '2026-11-23T07:10:00', 'some com a coleta', 'nova')
                    """,
                    (client, contact),
                )
                raise RuntimeError("imap caiu")

            calls: list[int] = []

            def accept(draft_id: int, channel: str) -> bool:
                calls.append(draft_id)
                return True

            opened = DayCycle(db, root).run(datetime(2026, 11, 23, 8, 1), accept, collect)
            self.assertTrue(opened.ran)
            self.assertEqual(calls, [draft])
            self.assertIsNone(
                db.execute("SELECT 1 FROM entrada WHERE texto = 'some com a coleta'").fetchone()
            )
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "enviado",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
            db.close()

    def test_search_and_the_clock_leave_the_caller_thread(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            home = threading.current_thread()
            started: list[threading.Thread] = []
            real = threading.Thread

            class Watching(real):
                def start(self) -> None:
                    started.append(self)
                    super().start()

            with patch("flow.controller.threading.Thread", Watching):
                self.assertEqual(controller.search("qualquer coisa"), 0)
                controller._start_background_cycle()
            for thread in started:
                thread.join(timeout=2)
            self.assertEqual(len(started), 2)
            self.assertTrue(all(thread.ident != home.ident for thread in started))
            controller.close()
