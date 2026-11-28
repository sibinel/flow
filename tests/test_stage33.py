"""Etapa 33: o ciclo não segura o banco na rede e não falha em silêncio."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.cycle import CycleConfig, DayCycle, config_path, save_config
from flow.notice import read_notice
from flow.store import open_database

_BRT = timezone(timedelta(hours=-3))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=_BRT)


def _seed(connection) -> tuple[int, int, int]:
    """Bruno com um processo, uma entrada nova que o cita e um rascunho aguardando."""
    bruno = connection.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
    ).lastrowid
    connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
    )
    phone = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511987654321')",
        (bruno,),
    ).lastrowid
    fresh = connection.execute(
        """
        INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado, canal)
        VALUES (NULL, ?, '2026-11-20T07:00:00-03:00', 'E a rescisão, doutor?', 'nova', 'whatsapp')
        """,
        (phone,),
    ).lastrowid
    old = connection.execute(
        """
        INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado, canal)
        VALUES (?, ?, '2026-11-19T07:00:00-03:00', 'antiga', 'arquivada', 'whatsapp')
        """,
        (bruno, phone),
    ).lastrowid
    waiting = connection.execute(
        "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Bruno, ok.', 'aguardando')",
        (old,),
    ).lastrowid
    connection.commit()
    return bruno, fresh, waiting


def _model(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class CycleLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "modelo.json").write_text(json.dumps({"api_key": "k"}), encoding="utf-8")
        self.db = open_database(self.data)
        self.bruno, self.fresh, self.waiting = _seed(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_approve_works_while_the_model_is_slow(self) -> None:
        with patch("flow.store.BUSY_TIMEOUT", 0.2):
            controller = Controller(self.data)
        started = threading.Event()
        release = threading.Event()
        outcome: dict = {}

        def slow(_url, _body, _headers):
            started.set()
            release.wait(5)
            outcome["model_done"] = True
            return _model("Bruno, a rescisão segue.")

        def run() -> None:
            cycle_db = open_database(self.data)
            try:
                with patch("flow.llm.post_chat", slow):
                    outcome["queue"] = DayCycle(cycle_db, self.data).run(_at(20, 8, 30))
            finally:
                cycle_db.close()

        worker = threading.Thread(target=run)
        try:
            with patch("flow.store.BUSY_TIMEOUT", 0.2):
                worker.start()
                self.assertTrue(started.wait(5))
                controller._reload()
                version = next(
                    row["approvalVersion"]
                    for row in controller._drafts._rows
                    if row["draftId"] == self.waiting
                )
                approved = controller.approve(self.waiting, version)
                approved_during_model = "model_done" not in outcome
        finally:
            release.set()
            worker.join(5)
            controller.close()
        self.assertTrue(approved)
        self.assertTrue(approved_during_model)
        self.assertIn("queue", outcome)
        drafts = self.db.execute(
            "SELECT texto FROM rascunho WHERE entrada_id = ?", (self.fresh,)
        ).fetchall()
        self.assertEqual([row["texto"] for row in drafts], ["Bruno, a rescisão segue."])

    def test_model_failure_keeps_entry_for_next_cycle(self) -> None:
        def down(_url, _body, _headers):
            raise RuntimeError("modelo caiu")

        with patch("flow.llm.post_chat", down):
            DayCycle(self.db, self.data).run(_at(20, 8, 30))
        row = self.db.execute(
            "SELECT estado, cliente_id FROM entrada WHERE id = ?", (self.fresh,)
        ).fetchone()
        self.assertEqual((row["estado"], row["cliente_id"]), ("nova", self.bruno))
        self.assertIn("modelo caiu", read_notice(self.data))

        with patch("flow.llm.post_chat", lambda *_args: _model("Bruno, segue.")):
            DayCycle(self.db, self.data).run(_at(21, 8, 30))
        drafts = self.db.execute(
            "SELECT texto FROM rascunho WHERE entrada_id = ?", (self.fresh,)
        ).fetchall()
        self.assertEqual([row["texto"] for row in drafts], ["Bruno, segue."])
        trechos = self.db.execute(
            "SELECT COUNT(*) FROM trecho WHERE entrada_id = ?", (self.fresh,)
        ).fetchone()[0]
        self.assertEqual(trechos, 1)

    def test_approve_reports_busy_database_instead_of_raising(self) -> None:
        with patch("flow.store.BUSY_TIMEOUT", 0.1):
            controller = Controller(self.data)
        blocker = open_database(self.data)
        try:
            controller._reload()
            version = next(
                row["approvalVersion"]
                for row in controller._drafts._rows
                if row["draftId"] == self.waiting
            )
            blocker.execute("BEGIN IMMEDIATE")
            self.assertFalse(controller.approve(self.waiting, version))
            self.assertIn("ocupado", read_notice(self.data))
        finally:
            blocker.rollback()
            blocker.close()
            controller.close()
        state = self.db.execute(
            "SELECT estado FROM rascunho WHERE id = ?", (self.waiting,)
        ).fetchone()["estado"]
        self.assertEqual(state, "aguardando")


class ScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.db = open_database(self.data)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_unsorted_times_still_run_both_slots(self) -> None:
        config = CycleConfig(2, ("14:00", "08:00"))
        self.assertEqual(config.slots(), ("08:00", "14:00"))
        ran = [
            DayCycle(self.db, self.data, config).run(moment).ran
            for moment in (_at(20, 8, 30), _at(20, 9, 0), _at(20, 14, 30), _at(20, 15, 0))
        ]
        self.assertEqual(ran, [True, False, True, False])

    def test_config_is_not_rewritten_on_every_tick(self) -> None:
        DayCycle(self.db, self.data)
        with patch("flow.cycle._write_atomic") as write:
            for _ in range(3):
                DayCycle(self.db, self.data)
        write.assert_not_called()

    def test_config_write_is_atomic_and_leaves_no_temp_file(self) -> None:
        save_config(self.data, CycleConfig(), "2026-11-20T08:00:00-03:00")
        with patch("flow.cycle.os.replace", side_effect=OSError("disco cheio")):
            with self.assertRaises(OSError):
                save_config(self.data, CycleConfig(), "2026-11-21T08:00:00-03:00")
        data = json.loads(config_path(self.data).read_text(encoding="utf-8"))
        self.assertEqual(data["ultimo"], "2026-11-20T08:00:00-03:00")
        self.assertEqual([item.name for item in self.data.glob(".ciclo.json-*")], [])


class BackgroundNoticeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.controller = Controller(self.data)

    def tearDown(self) -> None:
        self.controller.close()
        self.tmp.cleanup()

    def test_corrupted_config_is_reported(self) -> None:
        config_path(self.data).write_text("{ quebrado", encoding="utf-8")
        self.controller._run_background_cycle()
        self.assertIn("O ciclo falhou", read_notice(self.data))
        self.assertIn("O ciclo falhou", self.controller.notice)

    def test_any_background_failure_is_reported(self) -> None:
        with patch("flow.controller.DayCycle.run", side_effect=RuntimeError("boom")):
            self.controller._run_background_cycle()
        self.assertEqual(read_notice(self.data), "O ciclo falhou: boom")


if __name__ == "__main__":
    unittest.main()
