"""Etapa 21: a aprovação congela texto, destino e clientes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flow.controller import Controller
from flow.store import _migrate, open_database


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
            self.assertTrue(controller.approve(draft))

            db = open_database(root)
            db.execute(
                "UPDATE contato SET valor = 'outro@email.com', cliente_id = NULL"
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

    def test_divergent_copy_returns_to_the_queue(self) -> None:
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
            self.assertTrue(controller.approve(draft))
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

    def test_old_approved_row_receives_a_copy(self) -> None:
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
            db.commit()
            _migrate(db)
            row = db.execute(
                """
                SELECT texto_aprovado, destino_aprovado, canal_aprovado,
                       cliente_entrada_aprovado, cliente_contato_aprovado
                FROM rascunho WHERE id = ?
                """,
                (draft,),
            ).fetchone()
            self.assertEqual(row["texto_aprovado"], "Ana, confirmo.")
            self.assertEqual(row["destino_aprovado"], "ana.lima@email.com")
            self.assertEqual(row["canal_aprovado"], "email")
            self.assertEqual(row["cliente_entrada_aprovado"], client)
            self.assertEqual(row["cliente_contato_aprovado"], client)
            db.execute(
                "UPDATE contato SET valor = 'trocado@email.com' WHERE id = ?",
                (contact,),
            )
            db.commit()
            _migrate(db)
            again = db.execute(
                "SELECT destino_aprovado FROM rascunho WHERE id = ?",
                (draft,),
            ).fetchone()
            self.assertEqual(again["destino_aprovado"], "ana.lima@email.com")
            db.close()
