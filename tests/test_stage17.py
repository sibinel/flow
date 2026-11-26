"""Etapa 17: o envio não sai para o contato de outro cliente."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.store import open_database


class ForeignRecipientTest(unittest.TestCase):
    def test_indicated_client_keeps_the_other_phone_and_nothing_is_sent(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            controller = Controller(Path(name))
            db = open_database(controller.database_path.parent)
            ana = db.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
            ).lastrowid
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
            entry = db.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, processo_id, recebido_em, texto, estado
                ) VALUES (NULL, ?, NULL, '2026-11-23T08:00:00-03:00', 'Sou a Ana.', 'pendente')
                """,
                (phone,),
            ).lastrowid
            db.execute(
                "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, 'nome diferente')",
                (entry,),
            )
            mail = db.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'Ana, recebi.', 'aguardando')
                """,
                (entry,),
            ).lastrowid
            message = db.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'Ana, recebi no WhatsApp.', 'aguardando')
                """,
                (entry,),
            ).lastrowid
            db.commit()

            self.assertTrue(controller.indicate(entry, ana, 0))
            owner = db.execute(
                "SELECT cliente_id FROM contato WHERE id = ?",
                (phone,),
            ).fetchone()
            pointed = db.execute(
                "SELECT cliente_id FROM entrada WHERE id = ?",
                (entry,),
            ).fetchone()
            self.assertEqual(pointed["cliente_id"], ana)
            self.assertEqual(owner["cliente_id"], bruno)

            self.assertTrue(controller.approve(mail))
            self.assertTrue(controller.approve(message))

            def refuse(*_args, **_kwargs):
                raise AssertionError("entregou")

            self.assertFalse(
                controller.send_approved_mail(
                    mail, "smtp.example", 2525, "escritorio@flow.local", "segredo"
                )
            )
            self.assertFalse(controller.send_approved_whatsapp(message, refuse))

            states = {
                row["id"]: row["estado"]
                for row in db.execute("SELECT id, estado FROM rascunho")
            }
            self.assertEqual(states[mail], "aguardando")
            self.assertEqual(states[message], "aguardando")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM envio").fetchone()[0], 0)
            shown = db.execute(
                """
                SELECT contato.valor, contato.cliente_id
                FROM rascunho
                JOIN entrada ON entrada.id = rascunho.entrada_id
                JOIN contato ON contato.id = entrada.contato_id
                WHERE rascunho.estado = 'aguardando'
                """
            ).fetchall()
            self.assertEqual({row["valor"] for row in shown}, {"+5511987654321"})
            self.assertTrue(all(row["cliente_id"] == bruno for row in shown))
            db.close()
            controller.close()
