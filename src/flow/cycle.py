"""Ciclo do dia. Padrão de manhã. Fora do horário, nada sai."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from flow.classify import Classifier, ReadTool
from flow.llm import CloudLLM
from flow.when import parse_received

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

    def run(self, now: datetime, dispatch=None, collect=None) -> CycleQueue:
        """A vaga fica aberta até a próxima e roda uma vez. A coleta tem transação própria."""
        slot_at = self._open_slot(now)
        if slot_at is None or self._done(slot_at):
            return CycleQueue((), (), False, ())
        if collect is not None:
            try:
                collect()
                self._db.commit()
            except Exception:
                self._db.rollback()
        try:
            sent = self._execute(slot_at, dispatch)
            drafts, pendings = self._queue(slot_at)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        save_config(
            self._data_dir,
            self.config,
            slot_at.isoformat(timespec="seconds"),
        )
        return CycleQueue(drafts, pendings, True, tuple(sent))

    def _open_slot(self, now: datetime) -> datetime | None:
        """A vaga que já começou e ainda não chegou na seguinte."""
        slots = self.config.slots()
        if not slots:
            return None
        clock = now.strftime("%H:%M")
        started = [item for item in slots if item <= clock]
        if started:
            return _at(now, started[-1])
        previous = now - timedelta(days=1)
        return _at(previous, slots[-1])

    def _done(self, slot_at: datetime) -> bool:
        ultimo = self._ultimo()
        if ultimo is None:
            return False
        if ultimo.tzinfo is None and slot_at.tzinfo is not None:
            ultimo = ultimo.replace(tzinfo=slot_at.tzinfo)
        elif slot_at.tzinfo is None and ultimo.tzinfo is not None:
            slot_at = slot_at.replace(tzinfo=ultimo.tzinfo)
        return ultimo >= slot_at

    def _ultimo(self) -> datetime | None:
        path = config_path(self._data_dir)
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8")).get("ultimo")
        if not raw:
            return None
        return datetime.fromisoformat(raw)

    def _execute(self, now: datetime, dispatch) -> list[int]:
        self._hold_unreadable()
        archived: list[int] = []
        for entry_id in self._due(now):
            if Classifier(ReadTool(self._db)).classify(entry_id) == "arquivada":
                archived.append(entry_id)
        for entry_id in archived:
            self._followup(entry_id)
        return self._send_approved(now, dispatch)

    def _hold_unreadable(self) -> None:
        """Data que não dá para ler vira pendência e fica fora da vaga."""
        rows = self._db.execute(
            "SELECT id, recebido_em FROM entrada WHERE estado = 'nova' ORDER BY id"
        ).fetchall()
        for row in rows:
            if parse_received(row["recebido_em"]) is not None:
                continue
            self._db.execute(
                "UPDATE entrada SET estado = 'pendente' WHERE id = ?",
                (row["id"],),
            )
            found = self._db.execute(
                "SELECT 1 FROM pendencia WHERE entrada_id = ? AND motivo = 'data ilegível'",
                (row["id"],),
            ).fetchone()
            if found is None:
                self._db.execute(
                    "INSERT INTO pendencia (entrada_id, motivo) VALUES (?, 'data ilegível')",
                    (row["id"],),
                )

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
        entry = self._db.execute(
            "SELECT texto, cliente_id FROM entrada WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if entry is None or entry["cliente_id"] is None:
            return
        try:
            draft = CloudLLM(self._db).complete(
                "rascunho",
                client_id=int(entry["cliente_id"]),
                message=entry["texto"],
                query=entry["texto"],
            )
        except Exception:
            return
        text = draft.strip()
        if not text:
            return
        self._db.execute(
            """
            INSERT INTO rascunho (entrada_id, texto, estado)
            VALUES (?, ?, 'aguardando')
            """,
            (entry_id, text),
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
            if _already_sent(self._db, draft_id):
                continue
            if dispatch(draft_id, row["tipo"]) is not True:
                continue
            if not _already_sent(self._db, draft_id):
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


def _already_sent(connection: sqlite3.Connection, draft_id: int) -> bool:
    found = connection.execute(
        "SELECT 1 FROM envio WHERE rascunho_id = ?",
        (draft_id,),
    ).fetchone()
    return found is not None


def _at(now: datetime, slot: str) -> datetime:
    hour, minute = (int(part) for part in slot.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _at_or_before(value: str, now: datetime) -> bool:
    stamp = parse_received(value)
    if stamp is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=stamp.tzinfo)
    return stamp <= now.astimezone(stamp.tzinfo)
