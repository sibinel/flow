"""Instante da mensagem no fuso da máquina."""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime


def parse_received(value: str) -> datetime | None:
    """Lê ISO ou RFC 2822. Sem fuso, o valor é hora local. Data ilegível devolve None."""
    text = (value or "").strip()
    if not text:
        return None
    stamp = _iso(text)
    if stamp is None:
        stamp = _rfc2822(text)
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_here())
    return stamp.astimezone(_here())


def stored_received(value: str) -> str | None:
    """Formato único, com o fuso da máquina. None quando a data não dá para ler."""
    stamp = parse_received(value)
    if stamp is None:
        return None
    return stamp.isoformat(timespec="seconds")


def _here():
    return datetime.now().astimezone().tzinfo


def _iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rfc2822(text: str) -> datetime | None:
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
