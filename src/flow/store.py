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


def ensure_demo(connection: sqlite3.Connection) -> None:
    """Preenche a fila vazia uma vez. Não cria envio nem repete os exemplos."""
    queued = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM rascunho WHERE estado = 'aguardando')
          + (SELECT COUNT(*) FROM entrada WHERE estado = 'pendente')
        """
    ).fetchone()[0]
    if queued:
        return
    if connection.execute(
        "SELECT 1 FROM cliente WHERE nome = ?",
        ("Ana Lima",),
    ).fetchone():
        return

    ana = connection.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Ana Lima', 'cliente')"
    ).lastrowid
    bruno = connection.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Bruno Dias', 'cliente')"
    ).lastrowid
    connection.execute(
        "INSERT INTO cliente (nome, status) VALUES ('Carla Nunes', 'prospecção')"
    )
    ana_mail = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'email', 'ana.lima@email.com')",
        (ana,),
    ).lastrowid
    bruno_phone = connection.execute(
        "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, 'whatsapp', '+5511987654321')",
        (bruno,),
    ).lastrowid
    connection.execute(
        'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
        (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
    )
    ana_entry = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, '2026-11-18T09:00:00', ?, 'nova')
        """,
        (ana, ana_mail, "O contrato chegou."),
    ).lastrowid
    bruno_entry = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (?, ?, NULL, '2026-11-18T09:10:00', ?, 'nova')
        """,
        (bruno, bruno_phone, "A petição da rescisão ficou pronta."),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO rascunho (entrada_id, texto, estado) VALUES
            (?, 'Ana, o contrato chegou. Confirmo o recebimento?', 'aguardando'),
            (?, 'Bruno, a petição da rescisão está pronta. Posso enviar?', 'aguardando')
        """,
        (ana_entry, bruno_entry),
    )
    maria = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (NULL, ?, NULL, '2026-11-18T09:20:00', ?, 'pendente')
        """,
        (bruno_phone, "Sou a Maria, do celular do meu marido."),
    ).lastrowid
    contrato = connection.execute(
        """
        INSERT INTO entrada (
            cliente_id, contato_id, processo_id, recebido_em, texto, estado
        ) VALUES (NULL, ?, NULL, '2026-11-18T09:30:00', ?, 'pendente')
        """,
        (ana_mail, "Segue o contrato assinado para arquivar."),
    ).lastrowid
    connection.execute(
        "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, 'nome diferente'), (?, 'processo não citado')",
        (maria, contrato),
    )
    connection.commit()


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
