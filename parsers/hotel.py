"""Каталог отеля The Legend of Moscow: предложения, контакты и номера."""

# ruff: noqa: RUF001

import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Self

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "prepared"

BASE_URL = "https://thelegendofmoscow.com"
OFFERS_URL = f"{BASE_URL}/offers/"
CONTACTS_URL = f"{BASE_URL}/contacts/"
ROOMS_URL = f"{BASE_URL}/rooms/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
SKIP_TAGS = {"script", "style", "noscript"}
BREAK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "tr", "br"}

CatalogItem = dict[str, str]


class Element:
    def __init__(self, tag: str, attrs: dict[str, str], parent: Self | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Element | str] = []

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {}, None)
        self.stack: list[Element] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, _attr_map(attrs), self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def _attr_map(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name: value or "" for name, value in attrs}


def parse_html(html: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def walk(node: Element):
    yield node
    for child in node.children:
        if isinstance(child, Element):
            yield from walk(child)


def select(root: Element, *classes: str) -> list[Element]:
    required = set(classes)
    return [node for node in walk(root) if required <= node.classes()]


def select_one(root: Element, *classes: str) -> Element | None:
    found = select(root, *classes)
    return found[0] if found else None


def _is_button(node: Element) -> bool:
    if node.tag not in {"a", "button"}:
        return False
    classes = node.classes()
    return "button" in classes or any(name.startswith("button") for name in classes)


def render(node: Element) -> str:
    parts: list[str] = []
    _render(node, parts)
    return _cleanup("".join(parts))


def _render(node: Element, parts: list[str]) -> None:
    if node.tag in SKIP_TAGS or _is_button(node):
        return
    if node.tag in BREAK_TAGS:
        parts.append("\n")
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        else:
            _render(child, parts)


def _cleanup(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        folded = line.casefold()
        if folded.startswith("erid:") or "реклама, рекламодатель" in folded:
            continue
        lines.append(line)
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        blank = line == ""
        if blank and (previous_blank or not compact):
            continue
        compact.append(line)
        previous_blank = blank
    while compact and compact[-1] == "":
        compact.pop()
    return "\n".join(compact)


def _section_with_heading(root: Element, title: str) -> Element | None:
    for node in walk(root):
        if node.tag != "h2" or render(node) != title:
            continue
        parent = node.parent
        while parent is not None and parent.tag != "section":
            parent = parent.parent
        return parent
    return None


def fetch(url: str, attempts: int = 12) -> str:
    """Скачать HTML. Сайт иногда не принимает соединение, поэтому есть повтор."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru,en;q=0.8",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                html = response.read().decode(charset, errors="replace")
            print(url, flush=True)
            return html
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1)
    raise RuntimeError(f"Не удалось скачать {url}") from last_error


def _absolute(href: str) -> str:
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return f"{BASE_URL}{href if href.startswith('/') else '/' + href}"


def parse_offers(listing_html: str, fetch_page) -> list[CatalogItem]:
    root = parse_html(listing_html)
    offers: list[CatalogItem] = []
    seen: set[str] = set()
    for card in select(root, "card", "-type-default"):
        title = select_one(card, "card__title")
        summary = select_one(card, "card__text")
        link = _offer_link(card)
        if title is None or summary is None or link is None or link in seen:
            continue
        seen.add(link)
        offers.append(
            {
                "name": render(title),
                "description": render(summary),
                "fulltext": parse_offer_detail(fetch_page(link)),
            }
        )
    if not offers:
        raise ValueError("На странице предложений не найдено ни одной карточки")
    return offers


def _offer_link(card: Element) -> str | None:
    for node in walk(card):
        if node.tag != "a":
            continue
        href = node.attrs.get("href", "")
        if href.startswith("/offers/") and href.rstrip("/") != "/offers":
            return _absolute(href)
    return None


def parse_offer_detail(html: str) -> str:
    text = select_one(parse_html(html), "content__text")
    if text is None:
        raise ValueError("На странице предложения нет полного описания")
    fulltext = render(text)
    if not fulltext:
        raise ValueError("Полное описание предложения пустое")
    return fulltext


def parse_contacts(html: str) -> list[CatalogItem]:
    root = parse_html(html)
    column = select_one(root, "contact__col")
    if column is None:
        raise ValueError("На странице контактов нет блока связи")
    lines: list[str] = []
    legal = select_one(column, "contact__name-legal")
    if legal is not None:
        legal_name = render(legal)
        if legal_name:
            lines.append(legal_name)
    for cell in select(column, "contact__cell"):
        text = render(cell)
        if text:
            lines.append(text)
    fulltext = "\n".join(lines)
    if not fulltext:
        raise ValueError("Контакты на странице пустые")
    return [
        {
            "name": "Контакты для связи",
            "description": "Контакты для связи",
            "fulltext": fulltext,
        }
    ]


def parse_rooms(listing_html: str, fetch_page) -> list[CatalogItem]:
    root = parse_html(listing_html)
    rooms: list[CatalogItem] = []
    seen: set[str] = set()
    for card in select(root, "rooms-categories__room"):
        title = select_one(card, "room-card__title")
        link = select_one(card, "room-card__title-link")
        if title is None or link is None:
            continue
        href = link.attrs.get("href", "")
        if not href.startswith("/rooms/"):
            continue
        url = _absolute(href)
        if url in seen:
            continue
        seen.add(url)
        features = [render(item) for item in select(card, "room-card__feature")]
        description = "\n".join(line for line in features if line)
        rooms.append(
            {
                "name": render(title),
                "description": description,
                "fulltext": parse_room_detail(fetch_page(url)),
            }
        )
    if not rooms:
        raise ValueError("На странице номеров не найдено ни одной карточки")
    return rooms


def parse_room_detail(html: str) -> str:
    root = parse_html(html)
    parts: list[str] = []
    intro = select_one(root, "room-detail-heading__text")
    if intro is not None:
        intro_text = render(intro)
        if intro_text:
            parts.append(intro_text)
    for title in ("Подробности", "Дополнительные услуги"):
        section = _section_with_heading(root, title)
        if section is None:
            continue
        text = render(section)
        if text:
            parts.append(text)
    fulltext = "\n\n".join(parts)
    if not fulltext:
        raise ValueError("На странице номера нет описания")
    return fulltext


def _write(out_dir: Path, name: str, items: list[CatalogItem]) -> Path:
    path = out_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_catalog(out_dir: Path = OUT_DIR) -> list[Path]:
    """Скачать страницы отеля и записать special.json, contacts.json, rooms.json."""
    offers = parse_offers(fetch(OFFERS_URL), fetch)
    contacts = parse_contacts(fetch(CONTACTS_URL))
    rooms = parse_rooms(fetch(ROOMS_URL), fetch)
    return [
        _write(out_dir, "special.json", offers),
        _write(out_dir, "contacts.json", contacts),
        _write(out_dir, "rooms.json", rooms),
    ]


def main() -> None:
    for path in write_catalog():
        print(path)


if __name__ == "__main__":
    main()
