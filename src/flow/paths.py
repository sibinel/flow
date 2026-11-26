"""Pasta de dados do advogado, fora do pacote."""

from __future__ import annotations

import os
import sys
from pathlib import Path


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
    """Grava o arquivo e a linha. Se a linha falha, apaga o arquivo desta tentativa."""
    docs.mkdir(parents=True, exist_ok=True)
    target = document_target(docs, display_name, entry_id, index)
    target.write_bytes(payload)
    try:
        connection.execute(
            """
            INSERT INTO documento (entrada_id, caminho, nome)
            VALUES (?, ?, ?)
            """,
            (entry_id, str(target), display_name),
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def ensure_user_data(root: Path | None = None) -> Path:
    """Cria a pasta se faltar. Não apaga o que já está nela."""
    chosen = Path(root) if root is not None else default_user_data_dir()
    path = chosen.expanduser().resolve()
    pkg = package_dir().resolve()
    if _inside(path, pkg):
        raise ValueError("a pasta de dados não pode ficar dentro do pacote")
    path.mkdir(parents=True, exist_ok=True)
    return path
