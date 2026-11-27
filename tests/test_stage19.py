"""Etapa 19: em curso fica gravado antes da entrega."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.paths import whatsapp_session
from flow.store import _migrate, open_database


def _approve(controller: Controller, draft_id: int) -> None:
    controller._reload()
    row = next(row for row in controller._drafts._rows if row["draftId"] == draft_id)
    if not controller.approve(draft_id, str(row["approvalVersion"])):
        raise AssertionError("approval failed")


def _approved(controller: Controller) -> int:
    db = open_database(controller.database_path.parent)
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
        """
        INSERT INTO rascunho (entrada_id, texto, estado)
        VALUES (?, 'Ana, confirmo.', 'aguardando')
        """,
        (entry,),
    ).lastrowid
    db.commit()
    db.close()
    _approve(controller, int(draft))
    return int(draft)


class InProgressSendTest(unittest.TestCase):
    def test_false_returns_to_approved_and_the_next_try_sends(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            draft = _approved(controller)
            with patch("flow.send._deliver", return_value=False):
                self.assertFalse(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            db = open_database(Path(name))
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "aprovado",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            db.close()

            calls: list[int] = []

            def accept(draft_id: int, channel: str) -> bool:
                calls.append(draft_id)
                return True

            sent = controller.run_cycle(datetime(2026, 11, 23, 8, 0), accept)
            self.assertEqual(calls, [draft])
            self.assertEqual(sent.sent, (draft,))
            db = open_database(Path(name))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "enviado",
            )
            db.close()
            controller.close()

    def test_a_crash_stays_in_progress_and_is_not_sent_again(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            draft = _approved(controller)

            def crash(*_args, **_kwargs):
                raise RuntimeError("caiu no meio")

            with patch("flow.send._deliver", crash):
                with self.assertRaises(RuntimeError):
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )

            db = open_database(root)
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "em curso",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            db.close()

            roles = {
                bytes(role_name): role
                for role, role_name in controller.drafts.roleNames().items()
            }
            shown = []
            for row in range(controller.drafts.rowCount()):
                index = controller.drafts.index(row, 0)
                shown.append(
                    (
                        controller.drafts.data(index, roles[b"text"]),
                        controller.drafts.data(index, roles[b"address"]),
                        controller.drafts.data(index, roles[b"state"]),
                    )
                )
            self.assertIn(("Ana, confirmo.", "ana.lima@email.com", "em curso"), shown)

            calls: list[int] = []

            def refuse(draft_id: int, channel: str) -> bool:
                calls.append(draft_id)
                return True

            again = controller.run_cycle(datetime(2026, 11, 24, 8, 0), refuse)
            self.assertEqual(calls, [])
            self.assertEqual(again.sent, ())
            db = open_database(root)
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "em curso",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            db.close()
            controller.close()

    def test_whatsapp_false_returns_to_approved_without_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            contact = db.execute(
                """
                INSERT INTO contato (cliente_id, tipo, valor)
                VALUES (?, 'whatsapp', '+5511987654321')
                """,
                (client,),
            ).lastrowid
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'oi', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, ok.', 'aguardando')",
                (entry,),
            ).lastrowid
            db.commit()
            db.close()
            _approve(controller, int(draft))
            session = whatsapp_session(root)
            session.mkdir(parents=True)
            (session / "creds.json").write_text(
                json.dumps({"registered": True}),
                encoding="utf-8",
            )

            self.assertFalse(controller.send_approved_whatsapp(int(draft), lambda *_args: False))
            db = open_database(root)
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "aprovado",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            db.close()
            controller.close()

    def test_old_draft_table_accepts_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            db = open_database(Path(name))
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
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'oi', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'texto', 'aprovado')",
                (entry,),
            ).lastrowid
            db.commit()
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("ALTER TABLE rascunho RENAME TO rascunho_antigo")
            db.execute(
                """
                CREATE TABLE rascunho (
                    id INTEGER PRIMARY KEY,
                    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
                    texto TEXT NOT NULL,
                    estado TEXT NOT NULL CHECK (estado IN ('aguardando', 'aprovado', 'enviado'))
                )
                """
            )
            db.execute(
                """
                INSERT INTO rascunho (id, entrada_id, texto, estado)
                SELECT id, entrada_id, texto, estado FROM rascunho_antigo
                """
            )
            db.execute("DROP TABLE rascunho_antigo")
            db.commit()
            _migrate(db)
            db.execute(
                "UPDATE rascunho SET estado = 'em curso' WHERE id = ?",
                (draft,),
            )
            db.commit()
            self.assertEqual(
                db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()["estado"],
                "em curso",
            )
            db.close()
