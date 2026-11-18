"""Demonstração da fila, só quando ela ainda está vazia."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.store import ensure_demo, open_database


class DemoQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seeds_once_and_does_not_send(self) -> None:
        with open_database(self.data) as connection:
            ensure_demo(connection)
            ensure_demo(connection)
            drafts = connection.execute(
                "SELECT texto FROM rascunho WHERE estado = 'aguardando' ORDER BY id"
            ).fetchall()
            pendings = connection.execute(
                """
                SELECT entrada.texto, pendencia.motivo
                FROM entrada
                JOIN pendencia ON pendencia.entrada_id = entrada.id
                WHERE entrada.estado = 'pendente'
                ORDER BY entrada.id
                """
            ).fetchall()
            clients = connection.execute(
                "SELECT nome FROM cliente ORDER BY nome"
            ).fetchall()
            processes = connection.execute(
                'SELECT "número", "título" FROM processo'
            ).fetchall()
            sent = connection.execute("SELECT COUNT(*) FROM envio").fetchone()[0]

        self.assertEqual(
            [row["texto"] for row in drafts],
            [
                "Ana, o contrato chegou. Confirmo o recebimento?",
                "Bruno, a petição da rescisão está pronta. Posso enviar?",
            ],
        )
        self.assertEqual(
            [(row["texto"], row["motivo"]) for row in pendings],
            [
                ("Sou a Maria, do celular do meu marido.", "nome diferente"),
                ("Segue o contrato assinado para arquivar.", "processo não citado"),
            ],
        )
        self.assertEqual(
            [row["nome"] for row in clients],
            ["Ana Lima", "Bruno Dias", "Carla Nunes"],
        )
        self.assertEqual(len(processes), 1)
        self.assertEqual(processes[0]["título"], "Rescisão")
        self.assertEqual(sent, 0)

    def test_skips_a_queue_that_already_has_items(self) -> None:
        with open_database(self.data) as connection:
            client = connection.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Escritório', 'cliente')"
            ).lastrowid
            contact = connection.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ja@email.com')",
                (client,),
            ).lastrowid
            entry = connection.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, recebido_em, texto, estado
                ) VALUES (?, ?, '2026-11-18T08:00:00', 'já estava aqui', 'nova')
                """,
                (client, contact),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'texto que já estava na fila', 'aguardando')
                """,
                (entry,),
            )
            connection.commit()
            ensure_demo(connection)
            names = connection.execute("SELECT nome FROM cliente").fetchall()
        self.assertEqual([row["nome"] for row in names], ["Escritório"])

    def test_controller_with_a_given_folder_stays_empty(self) -> None:
        controller = Controller(self.data)
        try:
            self.assertEqual(controller.drafts.rowCount(), 0)
            self.assertEqual(controller.pendings.rowCount(), 0)
        finally:
            controller.close()
