"""Etapa 2: SQLite e FTS5 na pasta do usuário, fora do pacote."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flow.controller import Controller
from flow.paths import package_dir
from flow.store import database_file, open_database, search_trechos

TABLES = {
    "cliente",
    "contato",
    "processo",
    "entrada",
    "documento",
    "pendencia",
    "rascunho",
    "envio",
    "trecho",
    "trecho_fts",
}


def _seed(connection: sqlite3.Connection) -> None:
    cliente = connection.execute(
        "INSERT INTO cliente (nome, status) VALUES (?, ?)",
        ("João", "cliente"),
    ).lastrowid
    contato = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, ?, ?)",
        (cliente, "whatsapp", "+5511999990000"),
    ).lastrowid
    entrada = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (NULL, ?, NULL, ?, ?, 'pendente')
        """,
        (contato, "2026-11-18T10:00:00", "mensagem ainda sem cliente"),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO trecho (cliente_id, entrada_id, canal, quando, texto)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            cliente,
            entrada,
            "whatsapp",
            "2026-11-18T10:00:00",
            "Quando falei com o João sobre rescisão",
        ),
    )
    connection.commit()


class SchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "dados"
        self.controller = Controller(self.data)

    def tearDown(self) -> None:
        self.controller.close()
        self.tmp.cleanup()

    def test_tables_exist_outside_the_package(self) -> None:
        path = self.controller.database_path
        self.assertTrue(path.is_file())
        self.assertEqual(path, database_file(self.data).resolve())
        pkg = package_dir().resolve()
        self.assertFalse(pkg == path or pkg in path.parents)

        with open_database(self.data) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
        self.assertTrue(TABLES <= names)

    def test_entrada_keeps_client_and_process_optional(self) -> None:
        with open_database(self.data) as connection:
            cliente = connection.execute(
                "INSERT INTO cliente (nome, status) VALUES ('Ana', 'prospecção')"
            ).lastrowid
            contato = connection.execute(
                "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana@exemplo.com')",
                (cliente,),
            ).lastrowid
            entrada = connection.execute(
                """
                INSERT INTO entrada (
                    cliente_id, contato_id, processo_id, recebido_em, texto, estado
                ) VALUES (NULL, ?, NULL, '2026-11-18T11:00:00', 'oi', 'nova')
                """,
                (contato,),
            ).lastrowid
            row = connection.execute(
                "SELECT cliente_id, contato_id, processo_id FROM entrada WHERE id = ?",
                (entrada,),
            ).fetchone()
            connection.commit()
        self.assertIsNone(row["cliente_id"])
        self.assertIsNone(row["processo_id"])
        self.assertEqual(row["contato_id"], contato)

    def test_fts_finds_a_trecho_and_rebuild_keeps_it(self) -> None:
        with open_database(self.data) as connection:
            _seed(connection)
            found = search_trechos(connection, "rescisão")
        self.assertEqual(len(found), 1)
        self.assertIn("rescisão", found[0]["texto"])

        dist = self.root / "projeto" / "dist"
        dist.mkdir(parents=True)
        (dist / "Flow.app").mkdir()
        shutil.rmtree(dist)
        dist.mkdir()

        self.controller.close()
        self.controller = Controller(self.data)
        with open_database(self.data) as connection:
            again = search_trechos(connection, "rescisão")
        self.assertEqual(len(again), 1)
        self.assertTrue(self.controller.database_path.is_file())
        self.assertFalse(dist.resolve() == self.data or dist.resolve() in self.data.parents)


class WindowStaysOffTheDatabaseTest(unittest.TestCase):
    def test_qml_and_window_do_not_open_sqlite(self) -> None:
        qml = (package_dir() / "qml" / "Main.qml").read_text(encoding="utf-8").lower()
        window = (package_dir() / "window.py").read_text(encoding="utf-8").lower()
        for banned in ("sqlite", "flow.store", "imap", "smtp", "openai", "baileys"):
            self.assertNotIn(banned, qml)
            self.assertNotIn(banned, window)
