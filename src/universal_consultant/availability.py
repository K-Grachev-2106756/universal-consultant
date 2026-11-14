"""Таблица доступности: когда можно бронировать или записываться.

Строка таблицы — момент и признак доступности.
Для отеля момент — дата ночи проживания. Для поликлиники — дата и время приёма.
Вопросы по этой таблице собирает подготовка датасета.
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class AvailabilityRow:
    """Одна ячейка таблицы: дата или дата со временем и признак доступности."""

    date: date | datetime
    available: bool


def generate_availability(
    *,
    start: date | None = None,
    days: int = 14,
    with_time: bool = False,
    slot_minutes: int = 30,
    open_hours: Mapping[int, tuple[time, time]] | None = None,
    available_ratio: float = 0.5,
    seed: int = 0,
) -> list[AvailabilityRow]:
    """Собрать таблицу доступности.

    Без ``with_time`` в колонке ``date`` лежит ``datetime.date`` — день заезда.
    С ``with_time`` там ``datetime.datetime`` — начало окна записи.
    Вторая колонка — ``available``. Закрытый день из ``open_hours`` в таблицу не попадает.
    Если часы не переданы, а время нужно, берётся 09:00-18:00 каждый день.
    """
    if days < 1:
        raise ValueError("days должен быть не меньше 1.")
    if slot_minutes < 1:
        raise ValueError("slot_minutes должен быть не меньше 1.")
    if not 0 < available_ratio < 1:
        raise ValueError("available_ratio должен быть между 0 и 1.")

    origin = start.date() if isinstance(start, datetime) else (start or date.today())
    hours = open_hours
    if with_time and hours is None:
        hours = {weekday: (time(9, 0), time(18, 0)) for weekday in range(7)}

    rng = random.Random(seed)
    rows: list[AvailabilityRow] = []
    for offset in range(days):
        current = origin + timedelta(days=offset)
        window = None if hours is None else hours.get(current.weekday())
        if hours is not None and window is None:
            continue
        if window is not None and window[0] >= window[1]:
            raise ValueError("Интервал часов в расписании пустой.")
        if not with_time:
            rows.append(AvailabilityRow(current, rng.random() < available_ratio))
            continue
        if window is None:
            continue
        cursor = datetime.combine(current, window[0])
        finish = datetime.combine(current, window[1])
        step = timedelta(minutes=slot_minutes)
        while cursor < finish:
            rows.append(AvailabilityRow(cursor, rng.random() < available_ratio))
            cursor += step
    return rows
