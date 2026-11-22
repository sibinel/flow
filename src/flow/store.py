"""SQLite na pasta do usuário. Só o controller abre este módulo."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cliente (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prospecção', 'cliente', 'novo'))
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


_DEMO_CLIENTS = (
    ("Ana Lima", "cliente"),
    ("Bruno Dias", "cliente"),
    ("Carla Nunes", "prospecção"),
)
_DEMO_DRAFTS = (
    ("Ana Lima", "email", "ana.lima@email.com", "Ana, o contrato chegou. Confirmo o recebimento?"),
    ("Bruno Dias", "whatsapp", "+5511987654321", "Bruno, a petição da rescisão está pronta. Posso enviar?"),
)
_DEMO_PENDINGS = (
    ("Bruno Dias", "whatsapp", "+5511987654321", "Sou a Maria, do celular do meu marido.", "nome diferente"),
    ("Ana Lima", "email", "ana.lima@email.com", "Segue o contrato assinado para arquivar.", "processo não citado"),
)


def _row_id(connection: sqlite3.Connection, sql: str, params: tuple) -> int:
    found = connection.execute(sql, params).fetchone()
    return int(found["id"]) if found else 0


def _client_id(connection: sqlite3.Connection, nome: str, status: str) -> int:
    found = _row_id(connection, "SELECT id FROM cliente WHERE nome = ?", (nome,))
    if found:
        return found
    return int(
        connection.execute(
            "INSERT INTO cliente (nome, status) VALUES (?, ?)",
            (nome, status),
        ).lastrowid
    )


def _contact_id(connection: sqlite3.Connection, client_id: int, tipo: str, valor: str) -> int:
    found = _row_id(
        connection,
        "SELECT id FROM contato WHERE cliente_id = ? AND valor = ?",
        (client_id, valor),
    )
    if found:
        return found
    return int(
        connection.execute(
            "INSERT INTO contato (cliente_id, tipo, valor) VALUES (?, ?, ?)",
            (client_id, tipo, valor),
        ).lastrowid
    )


def ensure_demo(connection: sqlite3.Connection) -> None:
    """Inclui os exemplos que faltam. Não cria envio nem repete um texto."""
    clients = {
        nome: _client_id(connection, nome, status) for nome, status in _DEMO_CLIENTS
    }
    bruno = clients["Bruno Dias"]
    if not connection.execute(
        'SELECT 1 FROM processo WHERE cliente_id = ? AND "número" = ?',
        (bruno, "1001234-55.2024.8.26.0100"),
    ).fetchone():
        connection.execute(
            'INSERT INTO processo (cliente_id, "número", "título") VALUES (?, ?, ?)',
            (bruno, "1001234-55.2024.8.26.0100", "Rescisão"),
        )
    for nome, tipo, valor, texto in _DEMO_DRAFTS:
        found = connection.execute(
            "SELECT id, estado FROM rascunho WHERE texto = ?",
            (texto,),
        ).fetchone()
        if found:
            if found["estado"] != "aguardando":
                connection.execute(
                    "UPDATE rascunho SET estado = 'aguardando' WHERE id = ?",
                    (found["id"],),
                )
            continue
        contact = _contact_id(connection, clients[nome], tipo, valor)
        entry = connection.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, processo_id, recebido_em, texto, estado
            ) VALUES (?, ?, NULL, '2026-11-18T09:00:00', ?, 'nova')
            """,
            (clients[nome], contact, texto),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, ?, 'aguardando')
            """,
            (entry, texto),
        )
    for nome, tipo, valor, texto, motivo in _DEMO_PENDINGS:
        found = connection.execute(
            "SELECT id, estado FROM entrada WHERE texto = ?",
            (texto,),
        ).fetchone()
        if found:
            if found["estado"] != "pendente":
                connection.execute(
                    """
                    UPDATE entrada
                    SET estado = 'pendente', cliente_id = NULL, processo_id = NULL
                    WHERE id = ?
                    """,
                    (found["id"],),
                )
            if not connection.execute(
                "SELECT 1 FROM pendencia WHERE entrada_id = ? AND motivo = ?",
                (found["id"], motivo),
            ).fetchone():
                connection.execute(
                    "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, ?)",
                    (found["id"], motivo),
                )
            continue
        contact = _contact_id(connection, clients[nome], tipo, valor)
        entry = connection.execute(
            """
            INSERT INTO entrada (
                cliente_id, contato_id, processo_id, recebido_em, texto, estado
            ) VALUES (NULL, ?, NULL, '2026-11-18T09:20:00', ?, 'pendente')
            """,
            (contact, texto),
        ).lastrowid
        connection.execute(
            "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, ?)",
            (entry, motivo),
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
