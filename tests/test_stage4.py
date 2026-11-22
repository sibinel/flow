"""Etapa 4: classificador fixo, um cliente por vez, sem tool de envio."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from flow.classify import Classifier, ReadTool
from flow.controller import Controller
from flow.paths import package_dir
from flow.store import open_database


def _client(connection, name: str, status: str = "cliente") -> int:
    return connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, ?)",
        (name, status),
    ).lastrowid


def _contact(connection, client_id: int, value: str) -> int:
    return connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', ?)",
        (client_id, value),
    ).lastrowid


def _process(connection, client_id: int, number: str, title: str) -> int:
    return connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (client_id, number, title),
    ).lastrowid


def _entry(connection, contact_id: int, text: str) -> int:
    return connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (NULL, ?, NULL, '2026-11-22T10:00:00', ?, 'nova')
        """,
        (contact_id, text),
    ).lastrowid


class ClassifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.controller = Controller(Path(self.tmp.name))
        self.db = open_database(self.controller.database_path.parent)

    def tearDown(self) -> None:
        self.db.close()
        self.controller.close()
        self.tmp.cleanup()

    def _result(self, entry_id: int) -> tuple[str, int | None, int | None, int]:
        self.controller.classify_entry(entry_id)
        row = self.db.execute(
            """
            SELECT entrada.cliente_id, entrada.processo_id, entrada.contato_id,
                   pendencia.motivo
            FROM entrada
            LEFT JOIN pendencia ON pendencia.entrada_id = entrada.id
            WHERE entrada.id = ?
            """,
            (entry_id,),
        ).fetchone()
        return row["motivo"], row["cliente_id"], row["processo_id"], row["contato_id"]

    def test_three_pending_cases_and_no_send_tool(self) -> None:
        bruno = _client(self.db, "Bruno Dias")
        phone = _contact(self.db, bruno, "+5511987654321")
        _process(self.db, bruno, "1001234-55.2024.8.26.0100", "Rescisão")
        other = _client(self.db, "Ana Lima")
        other_process = _process(self.db, other, "9999", "Inventário")
        stranger = _client(self.db, "Número novo", "novo")
        new_phone = _contact(self.db, stranger, "+5511900000000")
        before = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        self.db.commit()

        wrong_name = _entry(self.db, phone, "Sou a Maria, do celular do meu marido.")
        new_contact = _entry(self.db, new_phone, "Olá, preciso de uma consulta.")
        missing_process = _entry(self.db, phone, "Segue o contrato assinado.")
        other_process_text = _entry(
            self.db, phone, "Sobre o processo 9999, o inventário."
        )
        clean = _entry(self.db, phone, "Bruno, segue a petição da rescisão.")
        self.db.commit()

        reason, client_id, process_id, contact_id = self._result(wrong_name)
        self.assertEqual(reason, "nome diferente")
        self.assertIsNone(client_id)
        self.assertIsNone(process_id)
        self.assertEqual(contact_id, phone)

        reason, client_id, process_id, contact_id = self._result(new_contact)
        self.assertEqual(reason, "contato novo")
        self.assertIsNone(client_id)
        self.assertIsNone(process_id)
        self.assertEqual(contact_id, new_phone)
        after = self.db.execute("SELECT COUNT(*) FROM cliente").fetchone()[0]
        self.assertEqual(after, before)

        reason, client_id, process_id, channel_id = self._result(missing_process)
        self.assertEqual(reason, "processo não citado")
        self.assertIsNone(client_id)
        self.assertIsNone(process_id)

        reason, client_id, process_id, channel_id = self._result(other_process_text)
        self.assertEqual(reason, "processo não citado")
        self.assertIsNone(client_id)
        self.assertNotEqual(process_id, other_process)

        self.controller.classify_entry(clean)
        filed = self.db.execute(
            "SELECT cliente_id, processo_id, estado FROM entrada WHERE id = ?",
            (clean,),
        ).fetchone()
        own_process = self.db.execute(
            "SELECT id FROM processo WHERE cliente_id = ?",
            (bruno,),
        ).fetchone()["id"]
        self.assertEqual(filed["estado"], "arquivada")
        self.assertEqual(filed["cliente_id"], bruno)
        self.assertEqual(filed["processo_id"], own_process)
        sent = self.db.execute("SELECT COUNT(*) FROM envio").fetchone()[0]
        self.assertEqual(sent, 0)

        read = ReadTool(self.db)
        classifier = Classifier(read)
        self.assertEqual(classifier.tool_names(), ("leitura",))
        self.assertNotIn("envio", classifier.tool_names())
        self.assertFalse(hasattr(classifier, "send"))
        classifier.classify(missing_process)
        self.assertEqual(read.loaded, [bruno])

    def test_window_does_not_call_the_classifier(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("classif", "openai", "imap", "smtp", "baileys"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
