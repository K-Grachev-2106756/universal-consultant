"""Карточка городской поликлиники: название, контакты, режим, специальности и сотрудники."""

# ruff: noqa: RUF001

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "prepared"

CLINIC_ID = "10000407"
CLINIC_URL = f"https://www.mos.ru/clinics/{CLINIC_ID}/"
CLINIC_API = f"https://www.mos.ru/api/clinics/v1/clinics/{CLINIC_ID}/"
POSITIONS_API = f"https://www.mos.ru/api/clinics/v1/clinics/{CLINIC_ID}/positions/"
SEARCH_API = f"https://www.mos.ru/aisearch/facility/employee/v1/search/{CLINIC_ID}/"
PAGE_SIZE = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

CatalogItem = dict[str, str]


def fetch_json(url: str, attempts: int = 5) -> dict[str, object]:
    """Скачать ответ. Сайт иногда обрывает соединение, поэтому есть повтор."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ru,en;q=0.8",
            "Referer": CLINIC_URL,
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            print(url, flush=True)
            if not isinstance(payload, dict):
                raise ValueError(f"Ответ {url} не объект JSON")
            return payload
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1)
    raise RuntimeError(f"Не удалось скачать {url}") from last_error


def format_phone(raw: str) -> str:
    """Привести номер к виду, который показывает карточка учреждения."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        digits = digits[1:]
    if len(digits) != 10:
        cleaned = raw.strip()
        if not cleaned:
            raise ValueError("Пустой телефон")
        return cleaned
    area, number = digits[:3], digits[3:]
    return f"+7 ({area}) {number[:3]}-{number[3:5]}-{number[5:]}"


def _require_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"В ответе нет объекта {key}")
    return value


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"В карточке пустое поле {key}")
    return value.strip()


def parse_clinic(payload: dict[str, object]) -> tuple[str, str, str]:
    data = _require_dict(payload, "data")
    name = _require_str(data, "name")
    address = _require_str(data, "address")
    phones = data.get("phones")
    if not isinstance(phones, list) or not phones:
        raise ValueError("В карточке нет телефонов")
    lines = [address]
    for phone in phones:
        if not isinstance(phone, str):
            raise ValueError("Телефон в карточке не строка")
        lines.append(format_phone(phone))
    schedule = _require_dict(data, "schedule")
    rendered = schedule.get("scheduleForRender")
    if not isinstance(rendered, dict) or not rendered:
        raise ValueError("В карточке нет режима работы")
    hours: list[str] = []
    for day, period in rendered.items():
        if not isinstance(day, str) or not isinstance(period, str) or not period.strip():
            raise ValueError("Режим работы пришёл в неожиданном виде")
        hours.append(f"{day}: {period.strip()}")
    return name, "\n".join(lines), "\n".join(hours)


def parse_positions(payload: dict[str, object]) -> list[tuple[int, str]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Список специальностей пуст")
    positions: list[tuple[int, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Специальность пришла не объектом")
        title = item.get("name")
        raw_id = item.get("id")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("У специальности нет названия")
        if not isinstance(raw_id, int):
            raise ValueError("У специальности нет номера")
        positions.append((raw_id, title.strip()))
    names = [title for _, title in positions]
    if len(set(names)) != len(names):
        raise ValueError("В списке специальностей есть повторы")
    positions.sort(key=lambda item: item[1])
    return positions


def specialists_url(position_id: int, page: int) -> str:
    query = urllib.parse.urlencode(
        {
            "position": position_id,
            "query": "",
            "group_size": PAGE_SIZE,
            "page": page,
        }
    )
    return f"{SEARCH_API}?{query}"


def _employee_page(payload: dict[str, object]) -> tuple[list[dict[str, object]], int]:
    facility = payload.get("facility")
    if not isinstance(facility, dict):
        raise ValueError("В ответе поиска нет учреждения")
    count = facility.get("count")
    if not isinstance(count, int) or count < 0:
        raise ValueError("В ответе поиска нет числа сотрудников")
    employees = facility.get("employees")
    if not isinstance(employees, dict):
        raise ValueError("В ответе поиска нет сотрудников")
    data = employees.get("data")
    if not isinstance(data, list):
        raise ValueError("Список сотрудников пришёл в неожиданном виде")
    people: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Сотрудник пришёл не объектом")
        people.append(item)
    return people, count


def _employee_name(person: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("last_name", "first_name", "middle_name"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if not parts:
        raise ValueError("У сотрудника нет имени")
    return " ".join(parts)


def fetch_specialists(positions: list[tuple[int, str]]) -> list[CatalogItem]:
    """Собрать сотрудников по каждой специальности из выпадающего списка."""
    rows: list[CatalogItem] = []
    seen: set[tuple[str, str]] = set()
    for position_id, title in positions:
        page = 1
        seen_ids: set[int] = set()
        total: int | None = None
        while True:
            people, count = _employee_page(fetch_json(specialists_url(position_id, page)))
            if total is None:
                total = count
            elif count != total:
                raise ValueError(f"Число сотрудников специальности {title} изменилось")
            if total == 0:
                break
            fresh: list[dict[str, object]] = []
            for person in people:
                raw_id = person.get("id")
                if not isinstance(raw_id, int):
                    raise ValueError("У сотрудника нет номера")
                if raw_id in seen_ids:
                    continue
                seen_ids.add(raw_id)
                fresh.append(person)
            if not fresh:
                if len(seen_ids) < total:
                    raise ValueError(f"Не удалось дочитать сотрудников специальности {title}")
                break
            for person in fresh:
                name = _employee_name(person)
                key = (name, title)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"name": name, "specialist": title})
            if len(seen_ids) >= total:
                break
            page += 1
            if page > total:
                raise ValueError(f"Не удалось дочитать сотрудников специальности {title}")
    if not rows:
        raise ValueError("Не найдено ни одного специалиста")
    rows.sort(key=lambda item: (item["specialist"], item["name"]))
    return rows


def build_catalog(clinic: dict[str, object], positions: list[tuple[int, str]]) -> list[CatalogItem]:
    name, contacts, hours = parse_clinic(clinic)
    specialties = "\n".join(title for _, title in positions)
    return [
        {
            "name": "Полное название",
            "description": "Полное название медучреждения",
            "fulltext": name,
        },
        {
            "name": "Контакты для связи",
            "description": "Контакты для связи",
            "fulltext": contacts,
        },
        {
            "name": "Режим работы",
            "description": "Время, когда работает поликлиника",
            "fulltext": hours,
        },
        {
            "name": "Предоставляемые услуги",
            "description": "Доступные врачи, доступные медицинские услуги",
            "fulltext": specialties,
        },
    ]


def _write(out_dir: Path, name: str, items: list[CatalogItem]) -> Path:
    path = out_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_catalog(out_dir: Path = OUT_DIR) -> list[Path]:
    """Скачать карточку поликлиники и записать подготовленный каталог."""
    positions = parse_positions(fetch_json(POSITIONS_API))
    info = build_catalog(fetch_json(CLINIC_API), positions)
    specialists = fetch_specialists(positions)
    return [
        _write(out_dir, "info.json", info),
        _write(out_dir, "specialists.json", specialists),
    ]


def main() -> None:
    for path in write_catalog():
        print(path)


if __name__ == "__main__":
    main()
