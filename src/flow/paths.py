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
    return Path.home() / ".local" / "share" / "flow"


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def ensure_user_data(root: Path | None = None) -> Path:
    """Cria a pasta se faltar. Não apaga o que já está nela."""
    chosen = Path(root) if root is not None else default_user_data_dir()
    path = chosen.expanduser().resolve()
    pkg = package_dir().resolve()
    if _inside(path, pkg):
        raise ValueError("a pasta de dados não pode ficar dentro do pacote")
    path.mkdir(parents=True, exist_ok=True)
    return path
