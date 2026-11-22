"""End-to-end tests against real ZIM archives built with libzim's own writer.

One archive has a full-text index, one has only a title index, and one file is not a ZIM at all.
Set ``OFFLINE_ZIM_REAL`` to the path of a downloaded ZIM (for example a Kiwix Wikipedia "mini"
file) to also run the smoke test against real-world content.
"""
import importlib
import json
import os
import socket
import sys
from pathlib import Path

import pytest
from libzim.writer import Creator, Hint, Item, StringProvider

ROOT = Path(__file__).resolve().parents[1]

LONG_PARAGRAPHS = [f"Paragraph {i}: " + " ".join(f"word{i}-{j}" for j in range(40)) for i in range(60)]

SCIENCE_PAGES = {
    "Main": ("Main page", "<html><body><h1>Science</h1><p>Start at <a href='Photosynthesis'>photosynthesis</a>.</p></body></html>"),
    "Photosynthesis": ("Photosynthesis", """<html><head><title>ignored</title><style>.x{color:red}</style></head>
<body><h1>Photosynthesis</h1>
<p>Plants turn <b>sunlight</b> into sugar using <a href="Chlorophyll">chlorophyll</a>
and light from <a href="./Sun#Light">the Sun</a>.</p>
<script>stealSecrets()</script>
<h2>Steps</h2><ul><li>Light reactions</li><li>Calvin cycle</li></ul>
<table><tr><th>Input</th><th>Output</th></tr><tr><td>CO2</td><td>O2</td></tr></table>
<p>See <a href="https://example.com/x">an outside page</a>, <a href="#top">the top</a>
and <a href="Chlorophyll">chlorophyll again</a>. Caf&eacute; &amp; more.</p>
</body></html>"""),
    "Chlorophyll": ("Chlorophyll", "<html><body><h1>Chlorophyll</h1><p>A green pigment that absorbs light.</p></body></html>"),
    "Sun": ("Sun", "<html><body><h1>Sun</h1><p>The star at the centre of the Solar System.</p></body></html>"),
    "Long": ("Long article", "<html><body><h1>Long</h1>" + "".join(f"<p>{p}</p>" for p in LONG_PARAGRAPHS) + "</body></html>"),
}


class _Page(Item):
    def __init__(self, path, title, content, mime="text/html"):
        super().__init__()
        self.path, self.title, self.content, self.mime = path, title, content, mime

    def get_path(self):
        return self.path

    def get_title(self):
        return self.title

    def get_mimetype(self):
        return self.mime

    def get_contentprovider(self):
        return StringProvider(self.content)

    def get_hints(self):
        return {Hint.FRONT_ARTICLE: self.mime == "text/html"}


def _build(path, metadata, pages, indexed, extra=()):
    with Creator(str(path)).config_indexing(indexed, "eng") as creator:
        creator.set_mainpath(next(iter(pages)))
        for key, value in metadata.items():
            creator.add_metadata(key, value)
        for page_path, (title, html) in pages.items():
            creator.add_item(_Page(page_path, title, html))
        for item in extra:
            creator.add_item(item)
        if "Photosynthesis" in pages:
            creator.add_redirection("Photo", "Photo", "Photosynthesis", {Hint.FRONT_ARTICLE: True})
    return path


@pytest.fixture(scope="session")
def library_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("zims")
    _build(root / "science_en.zim",
           {"Title": "Science Notes", "Description": "A tiny science library", "Language": "eng",
            "Creator": "Tests", "Publisher": "Tests", "Name": "science_en", "Date": "2026-11-23"},
           SCIENCE_PAGES, indexed=True,
           extra=[_Page("logo.png", "", "\x89PNG", "image/png"),
                  _Page("notes.txt", "Notes", "Plain text notes.\nSecond line.", "text/plain")])
    _build(root / "titles_en.zim",
           {"Title": "Titles Only", "Language": "eng", "Creator": "Tests", "Publisher": "Tests",
            "Name": "titles_en", "Date": "2026-11-23", "Description": "No full-text index"},
           {"Glacier": ("Glacier", "<html><body><h1>Glacier</h1><p>Slow ice.</p></body></html>"),
            "Glacier_melting": ("Glacier melting", "<html><body><p>Ice turns to water.</p></body></html>")},
           indexed=False)
    (root / "broken.zim").write_bytes(b"this is not a zim file")
    (root / "readme.txt").write_text("ignored: not a .zim file")
    return root


@pytest.fixture(scope="module")
def plugin():
    sys.path.insert(0, str(ROOT.parent))
    try:
        return importlib.import_module(ROOT.name)
    finally:
        sys.path.remove(str(ROOT.parent))


class _Ctx:
    """The slice of Hermes's PluginContext this plugin uses."""

    def __init__(self, settings):
        self.settings, self.tools = settings, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw


def _tools(plugin, settings):
    ctx = _Ctx(settings)
    plugin.register(ctx)
    return ctx.tools


def _run(tool, args):
    return json.loads(tool["handler"](args))


@pytest.fixture
def tools(plugin, library_dir):
    return _tools(plugin, {"paths": [str(library_dir)]})


def test_registers_exactly_the_declared_tools(plugin):
    manifest = (ROOT / "plugin.yaml").read_text()
    registered = _tools(plugin, {})
    assert sorted(registered) == ["zim_list_books", "zim_read", "zim_search"]
    for name, tool in registered.items():
        assert f"- {name}" in manifest and not tool.get("is_async")


def test_tools_are_offered_only_while_paths_are_configured(plugin, library_dir):
    assert _tools(plugin, {})["zim_search"]["check_fn"]() is False
    assert _tools(plugin, {"paths": []})["zim_search"]["check_fn"]() is False
    assert _tools(plugin, {"paths": [str(library_dir)]})["zim_read"]["check_fn"]() is True


def test_list_books_reads_each_archives_metadata(tools, library_dir):
    listed = _run(tools["zim_list_books"], {})
    by_id = {b["book"]: b for b in listed["books"]}
    assert sorted(by_id) == ["science_en", "titles_en"]
    assert by_id["science_en"]["title"] == "Science Notes"
    assert by_id["science_en"]["full_text_search"] is True and by_id["titles_en"]["full_text_search"] is False
    assert by_id["science_en"]["main_page"] == "Main" and by_id["science_en"]["language"] == "eng"
    assert by_id["science_en"]["articles"] >= 5
    assert [u.split(":")[0] for u in listed["unavailable"]] == [str(library_dir / "broken.zim")]


def test_full_text_search_returns_paths_and_plain_snippets(tools):
    found = _run(tools["zim_search"], {"query": "chlorophyll sunlight", "book": "science_en"})
    top = found["results"][0]
    assert (top["book"], top["path"], top["match"]) == ("science_en", "Photosynthesis", "full_text"), found
    assert "sunlight" in top["snippet"] and "<b>" not in top["snippet"]
    assert found["estimated_matches"]["science_en"] >= 1


def test_a_book_without_a_full_text_index_falls_back_to_titles(tools):
    found = _run(tools["zim_search"], {"query": "glacier", "book": "titles_en"})
    assert [(r["path"], r["match"]) for r in found["results"]] == [("Glacier", "title"), ("Glacier_melting", "title")]


def test_searching_every_book_interleaves_and_reports_the_broken_one(tools, library_dir):
    found = _run(tools["zim_search"], {"query": "glacier", "limit": 25})
    assert {r["book"] for r in found["results"]} == {"titles_en"}
    assert [u.split(":")[0] for u in found["unavailable"]] == [str(library_dir / "broken.zim")]
    limited = _run(tools["zim_search"], {"query": "light", "limit": 1})
    assert len(limited["results"]) == 1


def test_read_returns_readable_text_and_in_book_links(tools):
    page = _run(tools["zim_read"], {"book": "science_en", "path": "Photosynthesis"})
    text = page["text"]
    assert text.startswith("# Photosynthesis")
    assert "Plants turn sunlight into sugar using chlorophyll and light from the Sun." in text
    assert "## Steps" in text and "- Light reactions" in text and "- Calvin cycle" in text
    assert "Café & more." in text
    assert "stealSecrets" not in text and "color:red" not in text and "ignored" not in text
    assert page["links"] == [{"path": "Chlorophyll", "text": "chlorophyll"}, {"path": "Sun", "text": "the Sun"}]
    assert page["next_offset"] is None and page["total_chars"] == len(text)


def test_read_follows_redirects_and_accepts_a_title(tools):
    redirected = _run(tools["zim_read"], {"book": "science_en", "path": "Photo"})
    assert (redirected["path"], redirected["title"]) == ("Photosynthesis", "Photosynthesis")
    assert _run(tools["zim_read"], {"book": "science_en", "path": "Long article"})["path"] == "Long"
    assert _run(tools["zim_read"], {"book": "science_en", "path": "/Sun"})["title"] == "Sun"


def test_long_articles_are_read_in_parts_that_join_up(plugin, library_dir):
    read = _tools(plugin, {"paths": [str(library_dir / "science_en.zim")], "max_chars": 1000})["zim_read"]
    parts, offset = [], 0
    while offset is not None:
        part = _run(read, {"book": "science_en", "path": "Long", "offset": offset})
        assert len(part["text"]) <= 1000
        assert ("links" in part) is False
        parts.append(part["text"])
        offset = part["next_offset"]
    whole = "".join(parts)
    assert len(parts) > 2 and len(whole) == part["total_chars"]
    assert all(p in whole for p in LONG_PARAGRAPHS)


def test_plain_text_entries_are_returned_as_is(tools):
    assert _run(tools["zim_read"], {"book": "science_en", "path": "notes.txt"})["text"] == "Plain text notes.\nSecond line."


@pytest.mark.parametrize("args, message", [
    ({"book": "science_en", "path": "logo.png"}, "image/png, not a text article"),
    ({"book": "science_en", "path": "Nope"}, "use zim_search"),
    ({"book": "nope", "path": "Sun"}, "zim_list_books"),
    ({"book": "science_en"}, "path is required"),
    ({"path": "Sun"}, "book is required"),
    ({"book": "science_en", "path": "Sun", "offset": -1}, "offset must be"),
])
def test_read_errors_are_reported_not_raised(tools, args, message):
    assert message in _run(tools["zim_read"], args)["error"]


def test_search_errors_are_reported_not_raised(tools):
    assert "query is required" in _run(tools["zim_search"], {"query": " "})["error"]
    assert "limit must be" in _run(tools["zim_search"], {"query": "x", "limit": 26})["error"]
    assert "zim_list_books" in _run(tools["zim_search"], {"query": "x", "book": "nope"})["error"]


@pytest.mark.parametrize("settings, message", [
    ({}, "no ZIM paths are configured"),
    ({"paths": [1]}, "must be a list"),
    ({"paths": ["/tmp"], "max_chars": 10}, "max_chars"),
])
def test_invalid_settings_are_reported(plugin, settings, message):
    assert message in _run(_tools(plugin, settings)["zim_list_books"], {})["error"]


def test_missing_paths_and_duplicate_book_ids_are_reported(plugin, library_dir, tmp_path):
    twin = tmp_path / "twin"
    twin.mkdir()
    (twin / "science_en.zim").write_bytes((library_dir / "science_en.zim").read_bytes())
    listed = _run(_tools(plugin, {"paths": [str(library_dir), str(twin), str(tmp_path / "gone")]})["zim_list_books"], {})
    assert sorted(b["book"] for b in listed["books"]) == ["science_en", "titles_en"]
    problems = "\n".join(listed["unavailable"])
    assert "already" in problems and str(twin / "science_en.zim") in problems
    assert "not a .zim file or a folder" in problems


def test_nothing_goes_over_the_network(tools, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the plugin opened a network socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert _run(tools["zim_list_books"], {})["books"]
    assert _run(tools["zim_search"], {"query": "sun"})["results"]
    assert _run(tools["zim_read"], {"book": "science_en", "path": "Sun"})["text"]


@pytest.mark.skipif(not os.environ.get("OFFLINE_ZIM_REAL"), reason="set OFFLINE_ZIM_REAL to a downloaded .zim")
def test_a_real_world_archive(plugin):
    tools = _tools(plugin, {"paths": [os.environ["OFFLINE_ZIM_REAL"]]})
    (book,) = _run(tools["zim_list_books"], {})["books"]
    hits = _run(tools["zim_search"], {"query": "sea level rise", "book": book["book"]})["results"]
    assert hits, "a real archive returns search results"
    page = _run(tools["zim_read"], {"book": book["book"], "path": hits[0]["path"]})
    assert page["total_chars"] > 500 and page["links"]
    print(json.dumps({"book": book, "top_hit": hits[0], "first_chars": page["text"][:400],
                      "links": page["links"][:5]}, ensure_ascii=False, indent=1))
