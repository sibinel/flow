"""Etapa 11: a janela liga o ciclo, a busca e o pareamento pelo controller."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import package_dir, whatsapp_session
from flow.store import open_database
from flow.window import open_window

PHRASE = "Quando falei com o João sobre rescisão"
STOPPED = "Leitura e envio de WhatsApp estão parados."


def _client(connection, name: str) -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, 'cliente')",
        (name,),
    ).lastrowid


def _contact(connection, client_id: int, kind: str, value: str) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, ?, ?)",
        (client_id, kind, value),
    ).lastrowid


def _entry(connection, client_id, contact_id: int, text: str, when: str, state: str) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, ?, ?, ?)
        """,
        (client_id, contact_id, when, text, state),
    ).lastrowid


class WindowLinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def test_opening_the_window_does_not_send(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        email = _contact(self.db, bruno, "email", "bruno.dias@email.com")
        entry = _entry(
            self.db,
            bruno,
            email,
            "Pode confirmar.",
            "2026-11-22T09:00:00",
            "arquivada",
        )
        self.db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, 'Bruno, confirmo.', 'aprovado')
            """,
            (entry,),
        )
        self.db.commit()
        self.db.close()
        self.controller.close()

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        window = None
        try:
            _app, _engine, window = open_window(Path(self.tmp.name))
            self.assertEqual(_envio(window), 0)
            window._tick(datetime(2026, 11, 23, 15, 0))
            self.assertEqual(_envio(window), 0)
            state = open_database(window.database_path.parent)
            try:
                row = state.execute("SELECT estado FROM rascunho").fetchone()
                self.assertEqual(row["estado"], "aprovado")
            finally:
                state.close()
        finally:
            if window is not None:
                window.close()
            self.controller = Controller(Path(self.tmp.name))
            self.db = open_database(self.controller.database_path.parent)

        window_source = (package_dir() / "window.py").read_text(encoding="utf-8")
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8")
        self.assertIn("watch_schedule()", window_source)
        self.assertNotIn("run_cycle", window_source)
        self.assertNotIn("ciclo.json", window_source)
        self.assertIn("controller.search(searchField.text)", qml)
        self.assertIn("controller.pair_whatsapp()", qml)
        self.assertIn("controller.whatsappNotice", qml)
        self.assertIn("controller.qrImage", qml)
        self.assertIn("controller.approve(", qml)
        self.assertIn("controller.indicate(", qml)

    def test_outside_the_slot_nothing_leaves_and_nothing_is_collected(self) -> None:
        self._prepare_queue()
        self._write_mail_account()
        self._write_session()
        seen: list[str] = []

        def mail(self, host, port, user, password):
            seen.append("mail")
            return []

        def whatsapp(self):
            seen.append("whatsapp")
            return []

        with patch("flow.controller.ImapReader.ingest", mail), patch(
            "flow.controller.WhatsAppReader.ingest", whatsapp
        ):
            closed = self.controller._tick(datetime(2026, 11, 23, 15, 0))
        self.assertFalse(closed.ran)
        self.assertEqual(seen, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        self.assertEqual(
            self.db.execute("SELECT estado FROM entrada WHERE estado = 'nova'").fetchone()["estado"],
            "nova",
        )

    def test_on_the_slot_collection_runs_before_the_queue(self) -> None:
        _bruno, _phone, clean = self._prepare_queue()
        seen: list[object] = []

        def mail(self, host, port, user, password):
            seen.append(("mail", host, port, user, password))
            return []

        def whatsapp(self):
            seen.append(("whatsapp",))
            return []

        with patch("flow.controller.ImapReader.ingest", mail), patch(
            "flow.controller.WhatsAppReader.ingest", whatsapp
        ):
            skipped = self.controller._tick(datetime(2026, 11, 23, 8, 0))
        self.assertTrue(skipped.ran)
        self.assertEqual(seen, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
        follow = self.db.execute(
            "SELECT id, texto, estado FROM rascunho WHERE entrada_id = ?",
            (clean,),
        ).fetchone()
        self.assertEqual(follow["estado"], "aguardando")
        self.assertIn("rescisão", follow["texto"])
        self.assertIn(follow["id"], skipped.fila()["rascunho"])

        self.db.execute("DELETE FROM rascunho")
        self.db.execute(
            "UPDATE entrada SET estado = 'nova' WHERE id = ?",
            (clean,),
        )
        self.db.commit()
        path = Path(self.controller.dataDirectory) / "ciclo.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("ultimo", None)
        data["horarios"] = ["19:40"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_mail_account()
        self._write_session()
        seen.clear()

        with patch("flow.controller.ImapReader.ingest", mail), patch(
            "flow.controller.WhatsAppReader.ingest", whatsapp
        ):
            missed = self.controller._tick(datetime(2026, 11, 23, 8, 0))
            opened = self.controller._tick(datetime(2026, 11, 23, 19, 40))
        self.assertFalse(missed.ran)
        self.assertTrue(opened.ran)
        self.assertEqual(
            seen,
            [("mail", "imap.example.com", 993, "ana@example.com", "segredo"), ("whatsapp",)],
        )
        self.assertEqual(
            self.db.execute("SELECT estado FROM entrada WHERE id = ?", (clean,)).fetchone()["estado"],
            "arquivada",
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

    def test_search_becomes_a_draft_of_the_current_client(self) -> None:
        joao = _client(self.db, "João")
        maria = _client(self.db, "Maria Souza")
        joao_phone = _contact(self.db, joao, "whatsapp", "+5511987650001")
        maria_phone = _contact(self.db, maria, "whatsapp", "+5511987650002")
        about = "Falei com o João sobre a rescisão do contrato de trabalho."
        foreign = "A Maria comentou a rescisão no processo MARIA-UNICA."
        joao_entry = _entry(self.db, joao, joao_phone, about, "2026-11-18T10:00:00", "arquivada")
        maria_entry = _entry(self.db, maria, maria_phone, foreign, "2026-11-18T10:00:00", "arquivada")
        self.db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-18T10:00:00', ?)
            """,
            (joao, joao_entry, about),
        )
        self.db.execute(
            """
            INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
            VALUES (?, ?, 'whatsapp', '2026-11-18T10:00:00', ?)
            """,
            (maria, maria_entry, foreign),
        )
        self.db.commit()

        def transport(url: str, body: dict, headers: dict) -> dict:
            piece = json.loads(body["messages"][1]["content"])
            return {"choices": [{"message": {"content": "\n".join(piece["trechos"])}}]}

        draft_id = self.controller.search(PHRASE, transport=transport)
        draft = self.db.execute(
            "SELECT entrada_id, texto, estado FROM rascunho WHERE id = ?",
            (draft_id,),
        ).fetchone()
        self.assertEqual(draft["entrada_id"], joao_entry)
        self.assertEqual(draft["estado"], "aguardando")
        self.assertEqual(draft["texto"], about)
        self.assertNotIn("MARIA-UNICA", draft["texto"])
        self.assertEqual(self.controller.drafts.rowCount(), 1)
        roles = {bytes(name): role for role, name in self.controller.drafts.roleNames().items()}
        index = self.controller.drafts.index(0, 0)
        self.assertEqual(self.controller.drafts.data(index, roles[b"text"]), about)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)

    def test_pairing_without_a_session_asks_for_a_qr(self) -> None:
        self.assertEqual(self.controller.whatsappNotice, STOPPED)
        self.assertEqual(self.controller.qrImage, "")
        ops: list[str] = []

        def runner(session: Path, op: str, payload: dict) -> dict:
            ops.append(op)
            self.assertEqual(payload, {})
            (session / "qr.png").write_bytes(b"\x89PNG\r\n")
            return {"qr": True}

        self.controller.pair_whatsapp(runner)
        self.assertEqual(ops, ["pair"])
        self.assertTrue(self.controller.qrImage.endswith("qr.png"))
        self.assertEqual(self.controller.whatsappNotice, STOPPED)
        seen: list[str] = []

        def whatsapp(self):
            seen.append("whatsapp")
            return []

        with patch("flow.controller.WhatsAppReader.ingest", whatsapp):
            opened = self.controller._tick(datetime(2026, 11, 23, 8, 0))
        self.assertTrue(opened.ran)
        self.assertEqual(seen, [])

    def _prepare_queue(self) -> tuple[int, int, int]:
        bruno = _client(self.db, "Bruno Dias")
        phone = _contact(self.db, bruno, "whatsapp", "+5511987654321")
        self.db.execute(
            'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
            (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
        )
        clean = _entry(
            self.db,
            None,
            phone,
            "Bruno, segue a petição da rescisão.",
            "2026-11-23T07:00:00",
            "nova",
        )
        self.db.commit()
        return bruno, phone, clean

    def _write_mail_account(self) -> None:
        path = Path(self.controller.dataDirectory) / "email.json"
        path.write_text(
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

    def _write_session(self) -> None:
        session = whatsapp_session(Path(self.controller.dataDirectory))
        session.mkdir(parents=True, exist_ok=True)
        (session / "creds.json").write_text(
            json.dumps({"registered": True}),
            encoding="utf-8",
        )


def _envio(controller: Controller) -> int:
    connection = open_database(controller.database_path.parent)
    try:
        return connection.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
    finally:
        connection.close()
