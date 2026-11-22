"""Offline ZIM libraries for Hermes: list, search and read ZIM archives on this machine.

Configure ``plugins.entries.offline-zim.settings.paths`` with ZIM files or folders of them (see
README.md). The plugin registers three fixed tools, ``zim_list_books``, ``zim_search`` and
``zim_read``. They are offered to the model only while at least one path is configured and
python-libzim is installed. All reads are local; nothing goes over the network.
"""
from __future__ import annotations

import json
import logging

from . import library, schemas

logger = logging.getLogger(__name__)

TOOLSET = "offline_zim"


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str) -> str:
    return _json({"error": message})


def _libzim_available() -> bool:
    try:
        import libzim.reader  # noqa: F401
    except ImportError:
        return False
    return True


def register(ctx):
    def settings():
        paths = library.configured_paths(ctx.get_config("paths", default=[]))
        if not paths:
            raise library.SettingsError("no ZIM paths are configured (plugins.entries.offline-zim.settings.paths)")
        return paths, library.max_chars(ctx.get_config("max_chars", default=None))

    def books():
        paths, limit_chars = settings()
        found, problems = library.discover(paths)
        return found, problems, limit_chars

    def run(name, work):
        try:
            return _json(work())
        except (library.SettingsError, library.LookupFailed) as exc:
            return _error(str(exc))
        except Exception as exc:
            logger.warning("%s failed: %s", name, exc)
            return _error(f"{name} failed: {exc}")

    def list_books(args: dict, **kwargs) -> str:
        def work():
            found, problems, _ = books()
            return library.list_books(found, problems)
        return run("zim_list_books", work)

    def search(args: dict, **kwargs) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return _error("query is required")
        limit = args.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 25:
            return _error("limit must be an integer from 1 to 25")
        book = args.get("book")
        if book is not None and (not isinstance(book, str) or not book.strip()):
            return _error("book must be a book id from zim_list_books")

        def work():
            found, problems, _ = books()
            return library.search(found, problems, query, book.strip() if book else None, limit)
        return run("zim_search", work)

    def read(args: dict, **kwargs) -> str:
        book, path = args.get("book"), args.get("path")
        if not isinstance(book, str) or not book.strip():
            return _error("book is required (a book id from zim_list_books)")
        if not isinstance(path, str) or not path.strip():
            return _error("path is required (an article path from zim_search)")
        offset = args.get("offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return _error("offset must be a non-negative integer")

        def work():
            found, _problems, limit_chars = books()
            return library.read(found, book.strip(), path, offset, limit_chars)
        return run("zim_read", work)

    def available() -> bool:
        if not _libzim_available():
            return False
        try:
            return bool(settings()[0])
        except library.SettingsError:
            return False

    for name, schema, handler, description in (
        ("zim_list_books", schemas.ZIM_LIST_BOOKS, list_books, "List offline ZIM libraries"),
        ("zim_search", schemas.ZIM_SEARCH, search, "Search offline ZIM libraries"),
        ("zim_read", schemas.ZIM_READ, read, "Read an article from an offline ZIM library"),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler,
                          check_fn=available, description=description)
