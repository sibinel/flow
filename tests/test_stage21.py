"""Etapa 21: a aprovação congela texto, destino e clientes."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.send import SmtpSender
from flow.store import open_database


def _waiting(connection, client: int, address: str, text: str) -> int:
    contact = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', ?)",
        (client, address),
    ).lastrowid
    entry = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, recebido_em, texto, estado
        ) VALUES (?, ?, '2026-11-23T07:00:00', 'pedido', 'arquivada')
        """,
        (client, contact),
    ).lastrowid
    draft = connection.execute(
        """
        INSERT INTO rascunho (entrada_id, texto, estado)
        VALUES (?, ?, 'aguardando')
        """,
        (entry, text),
    ).lastrowid
    connection.commit()
    return int(draft)


class FrozenApprovalTest(unittest.TestCase):
    def _version(self, controller: Controller, draft_id: int) -> str:
        controller._reload()
        row = next(row for row in controller._drafts._rows if row["draftId"] == draft_id)
        return str(row["approvalVersion"])

    def test_send_uses_the_copy_from_approval(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            ana = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            draft = _waiting(db, int(ana), "ana.lima@email.com", "Ana, confirmo.")
            db.close()
            self.assertTrue(controller.approve(draft, self._version(controller, draft)))

            db = open_database(root)
            db.execute(
                "UPDATE contato SET valor = 'outro@email.com'"
            )
            db.execute(
                "UPDATE rascunho SET texto = 'texto trocado' WHERE id = ?",
                (draft,),
            )
            db.commit()
            db.close()

            seen: list[tuple[str, str]] = []

            def capture(*args):
                seen.append((args[3], args[4]))
                return True

            with patch("flow.send._deliver", capture):
                self.assertTrue(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            self.assertEqual(seen, [("ana.lima@email.com", "Ana, confirmo.")])
            controller.close()

    def test_divergent_clients_cannot_be_approved_or_sent(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            ana = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            other = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
            ).lastrowid
            draft = _waiting(db, int(ana), "ana.lima@email.com", "Ana, confirmo.")
            db.execute(
                "UPDATE entrada SET cliente_id = ? WHERE contato_id IS NOT NULL",
                (other,),
            )
            db.commit()
            db.close()
            self.assertFalse(controller.approve(draft, self._version(controller, draft)))
            with patch("flow.send._deliver", return_value=True) as deliver:
                self.assertFalse(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            deliver.assert_not_called()
            db = open_database(root)
            row = db.execute(
                "SELECT estado, texto_aprovado FROM rascunho WHERE id = ?",
                (draft,),
            ).fetchone()
            self.assertEqual(row["estado"], "aguardando")
            self.assertIsNone(row["texto_aprovado"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            db.close()
            controller.close()

    def test_legacy_approved_rows_return_to_review_once(self) -> None:
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
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-23T07:00:00', 'oi', 'arquivada')
                """,
                (client, contact),
            ).lastrowid
            draft = db.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'Ana, confirmo.', 'aprovado')
                """,
                (entry,),
            ).lastrowid
            copied = db.execute(
                """
                INSERT INTO rascunho (
                    entrada_id, texto, estado, texto_aprovado, destino_aprovado,
                    canal_aprovado, cliente_entrada_aprovado, cliente_contato_aprovado
                ) VALUES (?, 'Ana, cópia fabricada.', 'aprovado', 'Ana, cópia fabricada.',
                          'ana.lima@email.com', 'email', ?, ?)
                """,
                (entry, client, client),
            ).lastrowid
            in_progress = db.execute(
                """
                INSERT INTO rascunho (
                    entrada_id, texto, estado, texto_aprovado, destino_aprovado,
                    canal_aprovado, cliente_entrada_aprovado, cliente_contato_aprovado
                ) VALUES (?, 'incerto', 'em curso', 'incerto', 'ana.lima@email.com',
                          'email', ?, ?)
                """,
                (entry, client, client),
            ).lastrowid
            sent = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'já enviado', 'enviado')",
                (entry,),
            ).lastrowid
            db.execute(
                "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute("DELETE FROM store_meta WHERE key = 'legacy_approved_reset_v1'")
            db.commit()
            db.close()

            db = open_database(root)
            rows = {
                row["id"]: row
                for row in db.execute(
                    "SELECT id, estado, texto_aprovado, destino_aprovado FROM rascunho"
                )
            }
            for draft_id in (draft, copied):
                self.assertEqual(rows[draft_id]["estado"], "aguardando")
                self.assertIsNone(rows[draft_id]["texto_aprovado"])
                self.assertIsNone(rows[draft_id]["destino_aprovado"])
            self.assertEqual(rows[in_progress]["estado"], "em curso")
            self.assertEqual(rows[in_progress]["texto_aprovado"], "incerto")
            self.assertEqual(rows[sent]["estado"], "enviado")

            db.execute(
                "UPDATE rascunho SET estado = 'aprovado', texto_aprovado = 'novo aprovado' WHERE id = ?",
                (draft,),
            )
            db.commit()
            db.close()

            with open_database(root) as reopened:
                state = reopened.execute(
                    "SELECT estado, texto_aprovado FROM rascunho WHERE id = ?",
                    (draft,),
                ).fetchone()
                self.assertEqual(state["estado"], "aprovado")
                self.assertEqual(state["texto_aprovado"], "novo aprovado")

    def test_displayed_version_must_match_current_data(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            draft = _waiting(db, int(client), "ana.lima@email.com", "Ana, confirmo.")
            db.close()
            stale_version = self._version(controller, draft)

            db = open_database(root)
            db.execute("UPDATE rascunho SET texto = 'texto alterado' WHERE id = ?", (draft,))
            db.commit()
            db.close()

            self.assertFalse(controller.approve(draft, stale_version))
            with open_database(root) as reopened:
                row = reopened.execute(
                    "SELECT estado, texto_aprovado FROM rascunho WHERE id = ?", (draft,)
                ).fetchone()
                self.assertEqual(row["estado"], "aguardando")
                self.assertIsNone(row["texto_aprovado"])
            self.assertTrue(controller.approve(draft, self._version(controller, draft)))
            controller.close()

    def test_any_displayed_approval_field_change_requires_review(self) -> None:
        mutations = (
            "UPDATE rascunho SET texto = 'mudou' WHERE id = ?",
            "UPDATE contato SET valor = 'novo@example.com' WHERE id = (SELECT contato_id FROM entrada WHERE id = (SELECT entrada_id FROM rascunho WHERE id = ?))",
            "UPDATE contato SET tipo = 'whatsapp' WHERE id = (SELECT contato_id FROM entrada WHERE id = (SELECT entrada_id FROM rascunho WHERE id = ?))",
            "UPDATE entrada SET cliente_id = NULL WHERE id = (SELECT entrada_id FROM rascunho WHERE id = ?)",
            "UPDATE contato SET cliente_id = NULL WHERE id = (SELECT contato_id FROM entrada WHERE id = (SELECT entrada_id FROM rascunho WHERE id = ?))",
            "UPDATE cliente SET nome = 'nome alterado' WHERE id = (SELECT cliente_id FROM entrada WHERE id = (SELECT entrada_id FROM rascunho WHERE id = ?))",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                controller = Controller(root)
                db = open_database(root)
                client = db.execute(
                    "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
                ).lastrowid
                draft = _waiting(db, int(client), "ana@example.com", "Olá")
                version = self._version(controller, draft)
                db.execute(mutation, (draft,))
                db.commit()
                self.assertFalse(controller.approve(draft, version))
                self.assertEqual(
                    db.execute("SELECT estado FROM rascunho WHERE id = ?", (draft,)).fetchone()[0],
                    "aguardando",
                )
                db.close()
                controller.close()

    def test_two_controllers_cannot_approve_the_same_displayed_version(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            first = Controller(root)
            second = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            draft = _waiting(db, int(client), "ana.lima@email.com", "Ana, confirmo.")
            db.close()
            version = self._version(first, draft)

            outcomes = [
                first.approve(draft, version),
                second.approve(draft, version),
            ]
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 1)
            first.close()
            second.close()
            db.close()

    def test_smtp_rejects_wrong_channel_and_incomplete_approval_copy(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            phone = db.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511987654321')",
                (client,),
            ).lastrowid
            entry = db.execute(
                """
                INSERT INTO entrada (cliente_id, contato_id, recebido_em, texto, estado)
                VALUES (?, ?, '2026-11-23T07:00:00', 'oi', 'arquivada')
                """,
                (client, phone),
            ).lastrowid
            whatsapp_draft = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'Ana, oi.', 'aguardando')",
                (entry,),
            ).lastrowid
            incomplete = db.execute(
                "INSERT INTO rascunho (entrada_id, texto, estado) VALUES (?, 'sem cópia', 'aprovado')",
                (entry,),
            ).lastrowid
            db.commit()
            db.close()

            self.assertTrue(
                controller.approve(whatsapp_draft, self._version(controller, whatsapp_draft))
            )
            with patch("flow.send._deliver", return_value=True) as deliver:
                self.assertFalse(
                    controller.send_approved_mail(
                        whatsapp_draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
                self.assertFalse(
                    controller.send_approved_mail(
                        incomplete, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            deliver.assert_not_called()
            with open_database(root) as reopened:
                states = {
                    row["id"]: row["estado"]
                    for row in reopened.execute(
                        "SELECT id, estado FROM rascunho WHERE id IN (?, ?)",
                        (whatsapp_draft, incomplete),
                    )
                }
            self.assertEqual(states[whatsapp_draft], "aprovado")
            self.assertEqual(states[incomplete], "aprovado")
            controller.close()

    def test_multiple_email_recipients_are_held_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            draft = _waiting(
                db,
                int(client),
                "ana.lima@email.com, outro@email.com",
                "Ana, confirmo.",
            )
            db.close()
            self.assertTrue(controller.approve(draft, self._version(controller, draft)))
            with patch("flow.send._deliver", return_value=True) as deliver:
                self.assertFalse(
                    controller.send_approved_mail(
                        draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                    )
                )
            deliver.assert_not_called()
            with open_database(root) as reopened:
                row = reopened.execute(
                    "SELECT estado, destino_aprovado FROM rascunho WHERE id = ?", (draft,)
                ).fetchone()
                self.assertEqual(row["estado"], "aguardando")
                self.assertIsNone(row["destino_aprovado"])
            controller.close()

    def test_delivery_reservation_is_atomic_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            controller = Controller(root)
            db = open_database(root)
            client = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
            draft = _waiting(db, int(client), "ana.lima@email.com", "Ana, confirmo.")
            db.close()
            self.assertTrue(controller.approve(draft, self._version(controller, draft)))

            start = threading.Barrier(3)
            outcomes: list[bool] = []
            errors: list[Exception] = []

            def deliver() -> None:
                connection = open_database(root)
                start.wait()
                try:
                    outcomes.append(
                        SmtpSender(connection).send(
                            draft, "smtp.example", 465, "escritorio@flow.local", "segredo"
                        )
                    )
                except Exception as exc:
                    errors.append(exc)
                finally:
                    connection.close()

            with patch("flow.send._deliver", return_value=True) as send:
                first = threading.Thread(target=deliver)
                second = threading.Thread(target=deliver)
                first.start()
                second.start()
                start.wait()
                first.join(timeout=5)
                second.join(timeout=5)

            self.assertEqual(errors, [])
            self.assertEqual(outcomes.count(True), 1)
            self.assertEqual(outcomes.count(False), 1)
            send.assert_called_once()
            with open_database(root) as reopened:
                self.assertEqual(
                    reopened.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 1
                )
            controller.close()
