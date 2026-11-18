"""Etapa 3: a fila aprova rascunho e indica pendência, sem enviar."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.paths import package_dir
from flow.store import open_database


def _client(connection, name: str, status: str = "cliente") -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, ?)",
        (name, status),
    ).lastrowid


def _contact(connection, client_id: int) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
        (client_id, f"+55119{client_id:08d}"),
    ).lastrowid


def _process(connection, client_id: int, number: str) -> int:
    return connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (client_id, number, f"caso {number}"),
    ).lastrowid


def _entry(connection, contact_id: int, text: str) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (NULL, ?, NULL, '2026-11-18T12:00:00', ?, 'pendente')
        """,
        (contact_id, text),
    ).lastrowid


class QueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.controller.close()
        self.tmp.cleanup()

    def test_approve_marks_the_draft_and_does_not_send(self) -> None:
        with open_database(self.controller.database_path.parent) as connection:
            owner = _client(connection, "Dono")
            contact = _contact(connection, owner)
            entry = _entry(connection, contact, "segue o documento")
            draft = connection.execute(
                """
                INSERT INTO rascunho (entrada_id, texto, estado)
                VALUES (?, 'segue o combinado', 'aguardando')
                """,
                (entry,),
            ).lastrowid
            connection.commit()

        self.assertTrue(self.controller.approve(draft))
        self.assertFalse(self.controller.approve(draft))

        with open_database(self.controller.database_path.parent) as connection:
            state = connection.execute(
                "SELECT estado FROM rascunho WHERE id = ?",
                (draft,),
            ).fetchone()["estado"]
            sent = connection.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
        self.assertEqual(state, "aprovado")
        self.assertEqual(sent, 0)
        self.assertEqual(self.controller.drafts.rowCount(), 0)

    def test_indicate_records_the_client_and_optional_process(self) -> None:
        with open_database(self.controller.database_path.parent) as connection:
            owner = _client(connection, "Dono do canal")
            contact = _contact(connection, owner)
            target = _client(connection, "Cliente certo")
            process = _process(connection, target, "0001")
            other = _process(connection, owner, "9999")
            entry = _entry(connection, contact, "documento sem processo")
            only_client = _entry(connection, contact, "só o cliente")
            connection.execute(
                "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, 'processo não citado')",
                (entry,),
            )
            connection.commit()

        self.controller._reload()
        self.assertEqual(self.controller.pendings.rowCount(), 2)
        self.assertFalse(self.controller.indicate(entry, target, other))
        self.assertTrue(self.controller.indicate(entry, target, process))
        self.assertTrue(self.controller.indicate(only_client, target, 0))

        with open_database(self.controller.database_path.parent) as connection:
            filed = connection.execute(
                "SELECT cliente_id, processo_id, estado FROM entrada WHERE id = ?",
                (entry,),
            ).fetchone()
            client_only = connection.execute(
                "SELECT cliente_id, processo_id, estado FROM entrada WHERE id = ?",
                (only_client,),
            ).fetchone()
            sent = connection.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
        self.assertEqual(filed["cliente_id"], target)
        self.assertEqual(filed["processo_id"], process)
        self.assertEqual(filed["estado"], "arquivada")
        self.assertEqual(client_only["cliente_id"], target)
        self.assertIsNone(client_only["processo_id"])
        self.assertEqual(client_only["estado"], "arquivada")
        self.assertEqual(sent, 0)
        self.assertEqual(self.controller.pendings.rowCount(), 0)

    def test_select_client_lists_only_that_clients_processes(self) -> None:
        with open_database(self.controller.database_path.parent) as connection:
            first = _client(connection, "Ana")
            second = _client(connection, "Bruno")
            _process(connection, first, "1111")
            _process(connection, second, "2222")
            connection.commit()

        self.controller.selectClient(first)
        self.assertEqual(self.controller.processes.rowCount(), 2)
        label_role = next(
            role
            for role, name in self.controller.processes.roleNames().items()
            if bytes(name) == b"label"
        )
        label = self.controller.processes.data(
            self.controller.processes.index(1, 0),
            label_role,
        )
        self.assertIn("1111", label)
        self.assertNotIn("2222", label)


class WindowCallsOnlyTheControllerTest(unittest.TestCase):
    def test_qml_and_window_do_not_touch_store_model_or_channel(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8")
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        lowered = qml.lower()
        for banned in (
            "sqlite",
            "flow.store",
            "flow.queue",
            "imap",
            "smtp",
            "openai",
            "baileys",
            "langgraph",
            "qsql",
        ):
            self.assertNotIn(banned, lowered)
            self.assertNotIn(banned, window)

        calls = set(re.findall(r"controller\.(\w+)", qml))
        self.assertIn("approve", calls)
        self.assertIn("indicate", calls)
        self.assertTrue(
            calls
            <= {
                "approve",
                "indicate",
                "selectClient",
                "drafts",
                "pendings",
                "clients",
                "processes",
            }
        )
