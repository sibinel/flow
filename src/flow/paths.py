"""Pasta de dados do advogado, fora do pacote."""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path

_attempt_files: ContextVar[list | None] = ContextVar("flow_attempt_files", default=None)


def begin_files() -> list:
    """Lista os arquivos desta mensagem para apagar se ela falhar."""
    files: list = []
    _attempt_files.set(files)
    return files


def end_files() -> None:
    _attempt_files.set(None)


def package_dir() -> Path:
    """Diretório do código ou do executável empacotado."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Flow"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "Flow"
    return Path.home() / ".local" / "share" / "Flow"


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def whatsapp_session(data_dir: Path | None = None) -> Path:
    """Sessão Web do WhatsApp. Fora do pacote e fora da pasta de documentos.

    O SQLite e `documentos` são a cópia que a nuvem sincroniza.
    A sessão fica ao lado dessa pasta, não dentro dela.
    """
    data = (Path(data_dir) if data_dir is not None else default_user_data_dir()).expanduser().resolve()
    session = (data / "whatsapp-session").resolve()
    docs = (data / "documentos").resolve()
    pkg = package_dir().resolve()
    if _inside(session, pkg):
        raise ValueError("a sessão do WhatsApp não pode ficar dentro do pacote")
    if "documentos" in session.parts or _inside(session, docs):
        raise ValueError("a sessão do WhatsApp não pode ficar na pasta de documentos")
    return session


def document_target(docs: Path, filename: str, entry_id: int, index: int) -> Path:
    """Caminho único dentro de documentos, sem diretório vindo do remetente."""
    base = Path(filename).name
    if base in {"", ".", ".."}:
        base = "anexo"
    stem = Path(base).stem
    suffix = Path(base).suffix
    if stem in {"", ".", ".."}:
        stem = "anexo"
    marker = f"{entry_id}-{index}"
    candidate = docs / f"{stem}-{marker}{suffix}"
    extra = 2
    while candidate.exists():
        candidate = docs / f"{stem}-{marker}-{extra}{suffix}"
        extra += 1
    return candidate


def store_document(
    connection,
    docs: Path,
    display_name: str,
    entry_id: int,
    index: int,
    payload: bytes,
) -> Path:
    """Registra a tentativa antes de escrever e nunca sobrescreve outro arquivo."""
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection.execute("UPDATE documento SET caminho = caminho WHERE 0")
    docs.mkdir(parents=True, exist_ok=True)
    docs = docs.resolve()
    pending = docs / ".flow-pending"
    pending.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    target = document_target(docs, display_name, entry_id, index)
    stage = pending / f"{token}.part"
    journal = pending / f"{token}.json"
    with journal.open("x", encoding="utf-8") as file:
        json.dump({"target": target.name}, file)
        file.flush()
        os.fsync(file.fileno())
    attempt = _attempt_files.get()
    if attempt is not None:
        attempt.append((journal, stage, target))
    if hasattr(connection, "track_documents"):
        connection.track_documents(docs)
    try:
        _write_staged(stage, payload)
        os.link(stage, target)
        connection.execute(
            """
            INSERT INTO documento (entrada_id, caminho, nome)
            VALUES (?, ?, ?)
            """,
            (entry_id, str(target), display_name),
        )
    except Exception:
        if attempt is None:
            cleanup_attempt(connection, [(journal, stage, target)])
        raise
    return target


def _write_staged(stage: Path, payload: bytes) -> None:
    with stage.open("xb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def cleanup_attempt(connection, files: list) -> None:
    """Remove apenas caminhos registrados sem documento na transação atual."""
    for journal, stage, target in files:
        confirmed = connection.execute(
            "SELECT 1 FROM documento WHERE caminho = ? LIMIT 1", (str(target),)
        ).fetchone()
        if confirmed is None and stage.exists() and target.exists() and stage.samefile(target):
            target.unlink(missing_ok=True)
        stage.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)


def recover_documents(connection, docs: Path) -> None:
    """Reconcilia registros após commit, rollback ou interrupção, sob trava SQLite."""
    pending = docs.resolve() / ".flow-pending"
    if not pending.is_dir() or connection.in_transaction:
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        for journal in pending.glob("*.json"):
            token = journal.stem
            if len(token) != 32 or any(ch not in "0123456789abcdef" for ch in token):
                continue
            try:
                data = json.loads(journal.read_text(encoding="utf-8"))
                name = data["target"]
                if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
                    continue
                target = docs.resolve() / name
                cleanup_attempt(connection, [(journal, pending / f"{token}.part", target)])
            except (OSError, ValueError, KeyError, TypeError):
                continue
        try:
            pending.rmdir()
        except OSError:
            pass
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def ensure_user_data(root: Path | None = None) -> Path:
    """Cria a pasta se faltar. Não apaga o que já está nela."""
    chosen = Path(root) if root is not None else default_user_data_dir()
    path = chosen.expanduser().resolve()
    pkg = package_dir().resolve()
    if _inside(path, pkg):
        raise ValueError("a pasta de dados não pode ficar dentro do pacote")
    path.mkdir(parents=True, exist_ok=True)
    return path
