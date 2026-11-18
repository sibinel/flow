"""SQLite na pasta do usuário. Só o controller abre este módulo."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cliente (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prospecção', 'cliente'))
);

CREATE TABLE IF NOT EXISTS contato (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    tipo TEXT NOT NULL CHECK (tipo IN ('whatsapp', 'email')),
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processo (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    "número" TEXT NOT NULL,
    "título" TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entrada (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER REFERENCES cliente(id),
    contato_id INTEGER NOT NULL REFERENCES contato(id),
    processo_id INTEGER REFERENCES processo(id),
    recebido_em TEXT NOT NULL,
    texto TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('nova', 'pendente', 'arquivada'))
);

CREATE TABLE IF NOT EXISTS documento (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    caminho TEXT NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pendencia (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    motivo TEXT NOT NULL CHECK (motivo IN (
        'nome diferente', 'contato novo', 'processo não citado'
    ))
);

CREATE TABLE IF NOT EXISTS rascunho (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    texto TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('aguardando', 'aprovado', 'enviado'))
);

CREATE TABLE IF NOT EXISTS envio (
    id INTEGER PRIMARY KEY,
    rascunho_id INTEGER NOT NULL REFERENCES rascunho(id),
    enviado_em TEXT NOT NULL,
    canal TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trecho (
    id INTEGER PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES cliente(id),
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    canal TEXT NOT NULL,
    quando TEXT NOT NULL,
    texto TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS trecho_fts USING fts5(
    texto,
    content='trecho',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS trecho_ai AFTER INSERT ON trecho BEGIN
    INSERT INTO trecho_fts(rowid, texto) VALUES (new.id, new.texto);
END;

CREATE TRIGGER IF NOT EXISTS trecho_ad AFTER DELETE ON trecho BEGIN
    INSERT INTO trecho_fts(trecho_fts, rowid, texto)
    VALUES ('delete', old.id, old.texto);
END;

CREATE TRIGGER IF NOT EXISTS trecho_au AFTER UPDATE ON trecho BEGIN
    INSERT INTO trecho_fts(trecho_fts, rowid, texto)
    VALUES ('delete', old.id, old.texto);
    INSERT INTO trecho_fts(rowid, texto) VALUES (new.id, new.texto);
END;
"""


def database_file(data_dir: Path) -> Path:
    return data_dir / "flow.sqlite"


def open_database(data_dir: Path) -> sqlite3.Connection:
    """Cria o arquivo e o esquema se faltarem. Não apaga linhas existentes."""
    path = database_file(data_dir)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def search_trechos(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT trecho.id, trecho.cliente_id, trecho.entrada_id,
                   trecho.canal, trecho.quando, trecho.texto
            FROM trecho_fts
            JOIN trecho ON trecho.id = trecho_fts.rowid
            WHERE trecho_fts MATCH ?
            """,
            (query,),
        )
    )
