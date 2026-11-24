"""Ciclo do dia. Padrão de manhã. Fora do horário, nada sai."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from flow.classify import Classifier, ReadTool

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class CycleConfig:
    """Quantidade de ciclos no dia e os horários. O padrão é um, às 08:00."""

    quantity: int = 1
    times: tuple[str, ...] = ("08:00",)

    def __post_init__(self) -> None:
        if self.quantity < 1 or len(self.times) < self.quantity:
            raise ValueError("quantidade ou horário inválido")
        for item in self.times:
            if _TIME.fullmatch(item) is None:
                raise ValueError("horário inválido")

    def slots(self) -> tuple[str, ...]:
        return self.times[: self.quantity]


@dataclass(frozen=True)
class CycleQueue:
    rascunho: tuple[int, ...]
    pendencia: tuple[int, ...]
    ran: bool
    sent: tuple[int, ...]

    def fila(self) -> dict[str, tuple[int, ...]]:
        return {"rascunho": self.rascunho, "pendencia": self.pendencia}


def config_path(data_dir: Path) -> Path:
    return Path(data_dir) / "ciclo.json"


def load_config(data_dir: Path) -> CycleConfig:
    path = config_path(data_dir)
    if not path.is_file():
        return CycleConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    return CycleConfig(int(data["quantidade"]), tuple(data["horarios"]))


def save_config(data_dir: Path, config: CycleConfig, ultimo: str | None = None) -> None:
    path = config_path(data_dir)
    current: dict = {}
    if path.is_file():
        current = json.loads(path.read_text(encoding="utf-8"))
    payload: dict = {
        "quantidade": config.quantity,
        "horarios": list(config.times),
    }
    if ultimo is not None:
        payload["ultimo"] = ultimo
    elif current.get("ultimo"):
        payload["ultimo"] = current["ultimo"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class DayCycle:
    def __init__(
        self,
        connection: sqlite3.Connection,
        data_dir: Path,
        config: CycleConfig | None = None,
    ) -> None:
        self._db = connection
        self._data_dir = Path(data_dir)
        self.config = config or load_config(self._data_dir)
        save_config(self._data_dir, self.config)

    def run(self, now: datetime, dispatch=None) -> CycleQueue:
        """Monta a fila e envia o que já foi aprovado. Fora do horário, não envia."""
        slot = self._open_slot(now)
        if slot is None or self._done(now, slot):
            return CycleQueue((), (), False, ())
        try:
            sent = self._execute(now, dispatch)
            drafts, pendings = self._queue(now)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        save_config(
            self._data_dir,
            self.config,
            _at(now, slot).isoformat(timespec="seconds"),
        )
        return CycleQueue(drafts, pendings, True, tuple(sent))

    def _open_slot(self, now: datetime) -> str | None:
        stamp = now.strftime("%H:%M")
        if stamp not in self.config.slots():
            return None
        return stamp

    def _done(self, now: datetime, slot: str) -> bool:
        ultimo = self._ultimo()
        if ultimo is None:
            return False
        return ultimo >= _at(now, slot)

    def _ultimo(self) -> datetime | None:
        path = config_path(self._data_dir)
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8")).get("ultimo")
        if not raw:
            return None
        return datetime.fromisoformat(raw)

    def _execute(self, now: datetime, dispatch) -> list[int]:
        archived: list[int] = []
        for entry_id in self._due(now):
            if Classifier(ReadTool(self._db)).classify(entry_id) == "arquivada":
                archived.append(entry_id)
        for entry_id in archived:
            self._followup(entry_id)
        return self._send_approved(now, dispatch)

    def _due(self, now: datetime) -> list[int]:
        due: list[int] = []
        for row in self._db.execute(
            "SELECT id, recebido_em FROM entrada WHERE estado = 'nova' ORDER BY id"
        ):
            if _at_or_before(row["recebido_em"], now):
                due.append(int(row["id"]))
        return due

    def _followup(self, entry_id: int) -> None:
        found = self._db.execute(
            """
            SELECT 1 FROM rascunho
            WHERE entrada_id = ? AND estado = 'aguardando'
            """,
            (entry_id,),
        ).fetchone()
        if found is not None:
            return
        text = self._db.execute(
            "SELECT texto FROM entrada WHERE id = ?",
            (entry_id,),
        ).fetchone()["texto"]
        self._db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, ?, 'aguardando')
            """,
            (entry_id, f"Retorno sobre: {text}"),
        )

    def _send_approved(self, now: datetime, dispatch) -> list[int]:
        if dispatch is None:
            return []
        sent: list[int] = []
        rows = self._db.execute(
            """
            SELECT rascunho.id, contato.tipo, entrada.recebido_em
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            JOIN contato ON contato.id = entrada.contato_id
            WHERE rascunho.estado = 'aprovado'
            ORDER BY rascunho.id
            """
        )
        for row in rows:
            if not _at_or_before(row["recebido_em"], now):
                continue
            draft_id = int(row["id"])
            dispatch(draft_id, row["tipo"])
            sent_at = _at(now, now.strftime("%H:%M")).isoformat(timespec="seconds")
            self._db.execute(
                """
                INSERT INTO envio (rascunho_id, enviado_em, canal)
                VALUES (?, ?, ?)
                """,
                (draft_id, sent_at, row["tipo"]),
            )
            self._db.execute(
                "UPDATE rascunho SET estado = 'enviado' WHERE id = ?",
                (draft_id,),
            )
            sent.append(draft_id)
        return sent

    def _queue(self, now: datetime) -> tuple[tuple[int, ...], tuple[int, ...]]:
        drafts: list[int] = []
        for row in self._db.execute(
            """
            SELECT rascunho.id, entrada.recebido_em
            FROM rascunho
            JOIN entrada ON entrada.id = rascunho.entrada_id
            WHERE rascunho.estado = 'aguardando'
            ORDER BY rascunho.id
            """
        ):
            if _at_or_before(row["recebido_em"], now):
                drafts.append(int(row["id"]))
        pendings: list[int] = []
        for row in self._db.execute(
            """
            SELECT pendencia.id, entrada.recebido_em
            FROM pendencia
            JOIN entrada ON entrada.id = pendencia.entrada_id
            WHERE entrada.estado = 'pendente'
            ORDER BY pendencia.id
            """
        ):
            if _at_or_before(row["recebido_em"], now):
                pendings.append(int(row["id"]))
        return tuple(drafts), tuple(pendings)


def _at(now: datetime, slot: str) -> datetime:
    hour, minute = (int(part) for part in slot.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _at_or_before(value: str, now: datetime) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return True
    if stamp.tzinfo is not None and now.tzinfo is None:
        stamp = stamp.replace(tzinfo=None)
    elif stamp.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return stamp <= now
