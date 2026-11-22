"""Read ZIM archives (offline Wikipedia, Stack Exchange, Gutenberg, ...) with python-libzim.

Everything here is local: archives are opened from the paths the user configured, read, and dropped
when the call ends. Nothing goes over the network and nothing is written. Archives are opened per call
rather than cached, so a change in one profile's ``paths`` never serves another profile's books.
"""
from __future__ import annotations

import html as html_lib
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

DEFAULT_MAX_CHARS = 12_000
MAX_CHARS_LIMIT = 100_000
MAX_LINKS = 50
_TEXT_MIMES = ("text/html", "text/plain")
_BOOK_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class SettingsError(ValueError):
    """This plugin's settings cannot be used as written."""


class LookupFailed(ValueError):
    """A book or article the model asked for does not exist or is not readable text."""


def configured_paths(value: Any) -> list[Path]:
    """Validate ``settings.paths``: a list of ZIM files and/or folders holding ``*.zim`` files."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(p, str) and p.strip() for p in value):
        raise SettingsError("settings.paths must be a list of .zim files or folders")
    return [Path(p.strip()).expanduser() for p in value]


def max_chars(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_MAX_CHARS
    if not isinstance(value, int) or isinstance(value, bool) or not 1_000 <= value <= MAX_CHARS_LIMIT:
        raise SettingsError(f"settings.max_chars must be an integer from 1000 to {MAX_CHARS_LIMIT}")
    return value


def discover(paths: list[Path]) -> tuple[dict[str, Path], list[str]]:
    """Map book id (the file name without ``.zim``) to its file, plus problems found on the way.

    A folder is scanned one level deep for ``*.zim``. Ids must be unique: a second file with the
    same name in another folder is skipped and reported rather than silently shadowing the first.
    """
    books: dict[str, Path] = {}
    problems: list[str] = []
    for path in paths:
        if path.is_dir():
            files = sorted(p for p in path.iterdir() if p.suffix.lower() == ".zim" and p.is_file())
            if not files:
                problems.append(f"{path}: no .zim files in this folder")
        elif path.is_file() and path.suffix.lower() == ".zim":
            files = [path]
        else:
            problems.append(f"{path}: not a .zim file or a folder")
            continue
        for file in files:
            book = _BOOK_RE.sub("_", file.stem)
            if book in books:
                problems.append(f"{file}: skipped, book id {book!r} is already {books[book]}")
                continue
            books[book] = file
    return books, problems


def _open(file: Path):
    from libzim.reader import Archive
    return Archive(str(file))


def _metadata(archive, key: str) -> str | None:
    try:
        return bytes(archive.get_metadata(key)).decode("utf-8", errors="replace").strip() or None
    except (KeyError, RuntimeError):
        return None


def list_books(books: dict[str, Path], problems: list[str]) -> dict[str, Any]:
    out, unavailable = [], list(problems)
    for book, file in books.items():
        try:
            archive = _open(file)
        except Exception as exc:  # a damaged file must not hide the others
            unavailable.append(f"{file}: {exc}")
            continue
        entry = {"book": book, "title": _metadata(archive, "Title") or book,
                 "description": _metadata(archive, "Description"),
                 "language": _metadata(archive, "Language"),
                 "creator": _metadata(archive, "Creator"), "date": _metadata(archive, "Date"),
                 "articles": archive.article_count,
                 "full_text_search": bool(archive.has_fulltext_index),
                 "main_page": archive.main_entry.get_item().path if archive.has_main_entry else None}
        out.append({k: v for k, v in entry.items() if v is not None})
    result: dict[str, Any] = {"books": out}
    if unavailable:
        result["unavailable"] = unavailable
    return result


def _plain(fragment: str) -> str:
    """A search snippet without its <b> highlighting or other markup."""
    return _WS_RE.sub(" ", html_lib.unescape(_TAG_RE.sub("", fragment))).strip()


def search(books: dict[str, Path], problems: list[str], query: str, book: str | None, limit: int) -> dict[str, Any]:
    """Full-text search where an archive has an index, title suggestions where it has not."""
    if book is not None and book not in books:
        raise LookupFailed(f"no book {book!r}; zim_list_books lists the configured ones")
    targets = {book: books[book]} if book is not None else books
    per_book, unavailable, counts = [], list(problems), {}
    for name, file in targets.items():
        try:
            archive = _open(file)
            if archive.has_fulltext_index:
                from libzim.search import Query, Searcher
                found = Searcher(archive).search(Query().set_query(query))
                counts[name] = found.get_estimated_matches()
                hits = [{"book": name, "path": r.path, "title": r.title, "snippet": _plain(r.snippet),
                         "match": "full_text"} for r in found.get_results(0, limit)]
            else:
                from libzim.suggestion import SuggestionSearcher
                found = SuggestionSearcher(archive).suggest(query)
                counts[name] = found.get_estimated_matches()
                hits = []
                for path in found.get_results(0, limit):
                    entry = archive.get_entry_by_path(path)
                    hits.append({"book": name, "path": path, "title": entry.title, "match": "title"})
        except Exception as exc:
            unavailable.append(f"{file}: {exc}")
            continue
        per_book.append(hits)
    results = []  # interleave so one big archive does not crowd out the rest
    for rank in range(limit):
        for hits in per_book:
            if rank < len(hits) and len(results) < limit:
                results.append(hits[rank])
    payload: dict[str, Any] = {"results": results, "estimated_matches": counts}
    if unavailable:
        payload["unavailable"] = unavailable
    return payload


class _TextExtractor(HTMLParser):
    """HTML to readable text: headings, paragraphs and list items on their own lines, no markup."""

    _SKIP = {"script", "style", "noscript", "head", "template", "svg"}
    _BLOCK = {"p", "div", "section", "article", "br", "tr", "table", "blockquote", "dd", "dt",
              "figcaption", "caption", "ul", "ol", "dl", "hr", "pre", "details", "summary", "header",
              "footer", "aside", "main", "nav"}

    def __init__(self, base_path: str):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._seen_links: set[str] = set()
        self._skip = 0
        self._pre = 0
        self._link: tuple[str, list[str]] | None = None
        self._base = posixpath.dirname(base_path)

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre += 1
        if tag in self._BLOCK:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag == "a":
            target = self._target(dict(attrs).get("href"))
            self._link = (target, []) if target else None

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(self._skip - 1, 0)
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre = max(self._pre - 1, 0)
        if tag in self._BLOCK or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n")
        elif tag == "a" and self._link:
            target, words = self._link
            self._link = None
            if target not in self._seen_links and len(self.links) < MAX_LINKS:
                self._seen_links.add(target)
                self.links.append({"path": target, "text": _WS_RE.sub(" ", "".join(words)).strip()})

    def handle_data(self, data):
        if self._skip:
            return
        if self._link:
            self._link[1].append(data)
        self.parts.append(data if self._pre else _WS_RE.sub(" ", data.replace("\n", " ")))

    def _target(self, href: str | None) -> str | None:
        """The archive path an in-archive link points at, or None for external links and anchors."""
        if not href:
            return None
        parts = urlsplit(href)
        if parts.scheme or parts.netloc or not parts.path:
            return None
        path = unquote(parts.path)
        joined = posixpath.normpath(posixpath.join(self._base, path)) if not path.startswith("/") else path.lstrip("/")
        return None if joined.startswith("..") or joined in (".", "") else joined

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self.parts).split("\n")]
        out, blank = [], False
        for line in lines:
            if line:
                out.append(line)
                blank = False
            elif not blank and out:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def html_to_text(html_text: str, base_path: str) -> tuple[str, list[dict[str, str]]]:
    parser = _TextExtractor(base_path)
    parser.feed(html_text)
    parser.close()
    return parser.text(), parser.links


def read(books: dict[str, Path], book: str, path: str, offset: int, limit_chars: int) -> dict[str, Any]:
    """An article as plain text, ``limit_chars`` at a time from ``offset``, following redirects."""
    if book not in books:
        raise LookupFailed(f"no book {book!r}; zim_list_books lists the configured ones")
    archive = _open(books[book])
    path = path.strip().lstrip("/")
    try:
        entry = archive.get_entry_by_path(path)
    except KeyError:
        try:
            entry = archive.get_entry_by_title(path)  # a title is a fair guess at a path
        except KeyError:
            raise LookupFailed(f"no article {path!r} in {book}; use zim_search to find its path") from None
    while entry.is_redirect:
        entry = entry.get_redirect_entry()
    item = entry.get_item()
    mime = item.mimetype.split(";")[0].strip().lower()
    if mime not in _TEXT_MIMES:
        raise LookupFailed(f"{item.path} is {mime}, not a text article")
    raw = bytes(item.content).decode("utf-8", errors="replace")
    if mime == "text/html":
        text, links = html_to_text(raw, item.path)
    else:
        text, links = raw.strip(), []
    chunk = text[offset:offset + limit_chars]
    end = offset + len(chunk)
    payload: dict[str, Any] = {"book": book, "path": item.path, "title": entry.title or item.title,
                               "text": chunk, "offset": offset, "total_chars": len(text),
                               "next_offset": end if end < len(text) else None}
    if offset == 0 and links:
        payload["links"] = links
    return payload
