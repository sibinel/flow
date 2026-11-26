"""Etapa 13: um banco antigo, sem mensagem_id, abre e conserva as linhas."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from flow.store import database_file, open_database

OLD_SCHEMA = """
CREATE TABLE cliente (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prospecção', 'cliente', 'novo'))
);

CREATE TABLE contato (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('whatsapp', 'email')),
    valor TEXT NOT NULL
);

CREATE TABLE processo (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    "número" TEXT NOT NULL,
    "título" TEXT NOT NULL
);

CREATE TABLE entrada (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER REFERENCES cliente(id),
    contato_id INTEGER NOT NULL REFERENCES contato(id),
    processo_id INTEGER REFERENCES processo(id),
    recebido_em TEXT NOT NULL,
    texto TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('nova', 'pendente', 'arquivada'))
);

CREATE TABLE documento (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    caminho TEXT NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE pendencia (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    motivo TEXT NOT NULL CHECK (motivo IN (
        'nome diferente', 'contato novo', 'processo não citado'
    ))
);

CREATE TABLE rascunho (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    texto TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('aguardando', 'aprovado', 'enviado'))
);

CREATE TABLE envio (
    id INTEGER PRIMARY KEY,
    rascunho_id INTEGER NOT NULL REFERENCES rascunho(id),
    enviado_em TEXT NOT NULL,
    canal TEXT NOT NULL
);

CREATE TABLE trecho (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    canal TEXT NOT NULL,
    quando TEXT NOT NULL,
    texto TEXT NOT NULL
);
"""


class OldDatabaseTest(unittest.TestCase):
    def test_old_database_opens_and_keeps_its_rows(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path = database_file(root)
            with sqlite3.connect(path) as planted:
                planted.executescript(OLD_SCHEMA)
                planted.execute(
                    "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
                )
                planted.execute(
                    """
                    INSERT INTO contato (cliente_id, tipo, valor)
                    VALUES (1, 'email', 'ana.lima@email.com')
                    """
                )
                planted.execute(
                    """
                    INSERT INTO entrada (
                        cliente_id, contato_id, processo_id, recebido_em, texto, estado
                    ) VALUES (1, 1, NULL, '2026-11-18T10:00:00', 'segue o contrato', 'pendente')
                    """
                )
                planted.execute(
                    "INSERT INTO pendencia (entrada_id, motivo) VALUES (1, 'nome diferente')"
                )
                planted.commit()

            connection = open_database(root)
            connection.close()
            with open_database(root) as again:
                columns = {row[1] for row in again.execute("PRAGMA table_info(entrada)")}
                self.assertIn("mensagem_id", columns)
                index = again.execute(
                    """
                    SELECT sql FROM sqlite_master
                    WHERE type = 'index' AND name = 'entrada_mensagem_id'
                    """
                ).fetchone()
                self.assertIsNotNone(index)
                self.assertIn("mensagem_id", index["sql"])
                contato = {
                    row[1]: row for row in again.execute("PRAGMA table_info(contato)")
                }
                self.assertEqual(contato["cliente_id"][3], 0)
                kept = again.execute(
                    "SELECT nome FROM cliente WHERE id = 1"
                ).fetchone()
                self.assertEqual(kept["nome"], "Ana Lima")
                text = again.execute(
                    "SELECT texto FROM entrada WHERE id = 1"
                ).fetchone()
                self.assertEqual(text["texto"], "segue o contrato")
                reason = again.execute(
                    "SELECT motivo FROM pendencia WHERE entrada_id = 1"
                ).fetchone()
                self.assertEqual(reason["motivo"], "nome diferente")
                again.execute(
                    "INSERT INTO pendencia (entrada_id, motivo) VALUES (1, 'mídia não baixada')"
                )
                again.execute(
                    """
                    INSERT INTO contato (cliente_id, tipo, valor)
                    VALUES (NULL, 'email', 'novo@email.com')
                    """
                )
                again.commit()
