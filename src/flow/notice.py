"""Aviso curto na fila. A thread de fundo grava; a janela só lê."""

from __future__ import annotations

from pathlib import Path


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "aviso.txt"


def publish_notice(data_dir: Path, message: str) -> None:
    text = (message or "").strip() or "a busca falhou"
    _path(data_dir).write_text(text + "\n", encoding="utf-8")


def read_notice(data_dir: Path) -> str:
    path = _path(data_dir)
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def clear_notice(data_dir: Path) -> None:
    _path(data_dir).unlink(missing_ok=True)
