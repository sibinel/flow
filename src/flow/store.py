"""SQLite na pasta do usuário. Só o controller abre este módulo."""

from __future__ import annotations

import re
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
    cliente_id INTEGER REFERENCES cliente(id),
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
    estado TEXT NOT NULL CHECK (estado IN ('nova', 'pendente', 'arquivada')),
    mensagem_id TEXT,
    canal TEXT,
    conta TEXT
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
        'nome diferente', 'contato novo', 'processo não citado',
        'mídia não baixada', 'data ilegível'
    ))
);

CREATE TABLE IF NOT EXISTS rascunho (
    id INTEGER PRIMARY KEY,
    entrada_id INTEGER NOT NULL REFERENCES entrada(id),
    texto TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('aguardando', 'aprovado', 'enviado', 'em curso')),
    texto_aprovado TEXT,
    destino_aprovado TEXT,
    canal_aprovado TEXT,
    cliente_entrada_aprovado INTEGER,
    cliente_contato_aprovado INTEGER
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


def _scope_existing_messages(connection: sqlite3.Connection) -> None:
    """Preenche canal e conta nas linhas já gravadas. Não apaga nenhuma."""
    from flow.incoming import scoped_message_id

    rows = connection.execute(
        """
        SELECT entrada.id, entrada.mensagem_id, entrada.canal, entrada.conta,
               contato.tipo, contato.valor
        FROM entrada
        JOIN contato ON contato.id = entrada.contato_id
        """
    ).fetchall()
    for row in rows:
        canal = row["canal"] or row["tipo"]
        message_id = row["mensagem_id"]
        if row["tipo"] == "whatsapp":
            conta = row["conta"] or "whatsapp"
            scoped = scoped_message_id("whatsapp", row["valor"] or "", message_id or "")
            message_id = scoped or None
        else:
            conta = row["conta"] or row["valor"] or ""
        if (
            canal != row["canal"]
            or conta != row["conta"]
            or message_id != row["mensagem_id"]
        ):
            connection.execute(
                """
                UPDATE entrada
                SET canal = ?, conta = ?, mensagem_id = ?
                WHERE id = ?
                """,
                (canal, conta, message_id, row["id"]),
            )


def database_file(data_dir: Path) -> Path:
    return data_dir / "flow.sqlite"


def open_database(data_dir: Path) -> sqlite3.Connection:
    """Cria o arquivo e o esquema se faltarem. Não apaga linhas existentes."""
    path = database_file(data_dir)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    _migrate(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _freeze_approved(connection: sqlite3.Connection) -> None:
    """Copia texto, destino e clientes nos rascunhos que já estavam aprovados."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(rascunho)")}
    fields = {
        "texto_aprovado": "TEXT",
        "destino_aprovado": "TEXT",
        "canal_aprovado": "TEXT",
        "cliente_entrada_aprovado": "INTEGER",
        "cliente_contato_aprovado": "INTEGER",
    }
    for name, kind in fields.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE rascunho ADD COLUMN {name} {kind}")
    connection.execute(
        """
        UPDATE rascunho
        SET texto_aprovado = texto,
            destino_aprovado = (
                SELECT contato.valor
                FROM entrada
                JOIN contato ON contato.id = entrada.contato_id
                WHERE entrada.id = rascunho.entrada_id
            ),
            canal_aprovado = (
                SELECT contato.tipo
                FROM entrada
                JOIN contato ON contato.id = entrada.contato_id
                WHERE entrada.id = rascunho.entrada_id
            ),
            cliente_entrada_aprovado = (
                SELECT entrada.cliente_id
                FROM entrada
                WHERE entrada.id = rascunho.entrada_id
            ),
            cliente_contato_aprovado = (
                SELECT contato.cliente_id
                FROM entrada
                JOIN contato ON contato.id = entrada.contato_id
                WHERE entrada.id = rascunho.entrada_id
            )
        WHERE estado IN ('aprovado', 'em curso')
          AND texto_aprovado IS NULL
        """
    )


def _migrate(connection: sqlite3.Connection) -> None:
    """Acrescenta o id da mensagem e o contato sem cliente em bancos já criados."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(entrada)")}
    if "mensagem_id" not in columns:
        connection.execute("ALTER TABLE entrada ADD COLUMN mensagem_id TEXT")
    if "canal" not in columns:
        connection.execute("ALTER TABLE entrada ADD COLUMN canal TEXT")
    if "conta" not in columns:
        connection.execute("ALTER TABLE entrada ADD COLUMN conta TEXT")
    _scope_existing_messages(connection)
    connection.execute("DROP INDEX IF EXISTS entrada_mensagem_id")
    connection.execute(
        """
        CREATE UNIQUE INDEX entrada_mensagem_id
        ON entrada(canal, conta, mensagem_id)
        WHERE mensagem_id IS NOT NULL AND mensagem_id != ''
        """
    )
    contato = list(connection.execute("PRAGMA table_info(contato)"))
    cliente = next(row for row in contato if row[1] == "cliente_id")
    if cliente[3]:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE contato_migrado (
                id INTEGER PRIMARY KEY,
                cliente_id INTEGER REFERENCES cliente(id),
                tipo TEXT NOT NULL CHECK (tipo IN ('whatsapp', 'email')),
                valor TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO contato_migrado (id, cliente_id, tipo, valor)
            SELECT id, cliente_id, tipo, valor FROM contato
            """
        )
        connection.execute("DROP TABLE contato")
        connection.execute("ALTER TABLE contato_migrado RENAME TO contato")
        connection.execute("PRAGMA foreign_keys = ON")
    pendencia = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pendencia'"
    ).fetchone()
    if pendencia is not None and "data ilegível" not in pendencia["sql"]:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE pendencia_migrada (
                id INTEGER PRIMARY KEY,
                entrada_id INTEGER NOT NULL REFERENCES entrada(id),
                motivo TEXT NOT NULL CHECK (motivo IN (
                    'nome diferente', 'contato novo', 'processo não citado',
                    'mídia não baixada', 'data ilegível'
                ))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pendencia_migrada (id, entrada_id, motivo)
            SELECT id, entrada_id, motivo FROM pendencia
            """
        )
        connection.execute("DROP TABLE pendencia")
        connection.execute("ALTER TABLE pendencia_migrada RENAME TO pendencia")
        connection.execute("PRAGMA foreign_keys = ON")
    rascunho = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'rascunho'"
    ).fetchone()
    if rascunho is not None and "em curso" not in rascunho["sql"]:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            CREATE TABLE rascunho_migrado (
                id INTEGER PRIMARY KEY,
                entrada_id INTEGER NOT NULL REFERENCES entrada(id),
                texto TEXT NOT NULL,
                estado TEXT NOT NULL CHECK (
                    estado IN ('aguardando', 'aprovado', 'enviado', 'em curso')
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO rascunho_migrado (id, entrada_id, texto, estado)
            SELECT id, entrada_id, texto, estado FROM rascunho
            """
        )
        connection.execute("DROP TABLE rascunho")
        connection.execute("ALTER TABLE rascunho_migrado RENAME TO rascunho")
        connection.execute("PRAGMA foreign_keys = ON")
    _freeze_approved(connection)
    connection.commit()


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
            "SELECT id FROM rascunho WHERE texto = ?",
            (texto,),
        ).fetchone()
        if found:
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
            "SELECT id FROM entrada WHERE texto = ?",
            (texto,),
        ).fetchone()
        if found:
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


def search_trechos_do_cliente(
    connection: sqlite3.Connection, client_id: int, query: str
) -> list[sqlite3.Row]:
    """FTS5 só nos trechos daquele cliente. Outro cliente não entra."""
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if client_id <= 0 or not tokens:
        return []
    return list(
        connection.execute(
            """
            SELECT trecho.id, trecho.cliente_id, trecho.entrada_id,
                   trecho.canal, trecho.quando, trecho.texto
            FROM trecho_fts
            JOIN trecho ON trecho.id = trecho_fts.rowid
            WHERE trecho_fts MATCH ? AND trecho.cliente_id = ?
            ORDER BY trecho.id
            """,
            (" ".join(tokens), client_id),
        )
    )
