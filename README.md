# hermes-offline-zim

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that lets the agent search
and read **ZIM** files on your own machine, without internet access. ZIM is the open archive format
behind the offline libraries [Kiwix](https://kiwix.org/) publishes: full Wikipedia in many
languages, Wiktionary, Wikivoyage, Stack Exchange sites, Project Gutenberg, documentation sets and
more.

Useful when the agent runs somewhere with no or poor connectivity, when you want answers grounded in
a fixed, known copy of a reference work, or when you would rather not send every lookup to a web
search provider.

This plugin is not affiliated with Kiwix or openZIM. It reads ZIM files with the
[python-libzim](https://github.com/openzim/python-libzim) bindings.

## Tools

| Tool | What it does |
|---|---|
| `zim_list_books` | Lists the configured ZIM files: book id, title, description, language, date, article count, whether it has a full-text index, and its main page. |
| `zim_search` | Searches one book or all of them. Uses a book's full-text index when it has one (results carry a plain-text snippet) and falls back to matching article titles when it does not. |
| `zim_read` | Reads one article as plain text, with headings marked `#` and list items `-`. Redirects are followed and an exact title works in place of a path. Long articles come back in parts (`next_offset`). The first part also lists the article's links to other articles in the same book. |

The tools are offered to the model only while at least one path is configured and `libzim` is
installed.

## Install

```bash
hermes plugins install offline-zim --enable
```

The plugin depends on `libzim` (declared in `pyproject.toml`). Hermes asks before installing it.
`libzim` ships prebuilt wheels for Linux (glibc and musl), macOS and Windows.

## Get some ZIM files

Download them from the [Kiwix library](https://library.kiwix.org/) or
[download.kiwix.org](https://download.kiwix.org/zim/). Files range from a few megabytes (for example
`wikipedia_en_climate-change_mini`) to around 100 GB (all of English Wikipedia with images). The
`nopic` and `mini` variants are much smaller and read the same way, since this plugin returns text
only.

## Configure

```yaml
plugins:
  entries:
    offline-zim:
      settings:
        paths:                     # ZIM files and/or folders holding .zim files
          - ~/zims
          - /data/wikipedia_en_all_nopic_2026-10.zim
        max_chars: 12000           # optional; characters per zim_read call (1000-100000)
```

A folder is scanned one level deep for `*.zim`. Each file becomes a book whose id is its file name
without `.zim`, for example `wikipedia_en_all_nopic_2026-10`. If two folders hold files with the same
name, the first one wins and the second is reported under `unavailable`. Missing paths and damaged
files are reported the same way and do not hide the other books.

## What it does on your machine (disclosure)

- **Reads only the ZIM files you configure.** Nothing else on disk is opened. Folders are listed
  only to find `*.zim` files.
- **No network.** All search and reading happens locally through libzim. The test suite checks this
  by refusing to open any socket while all three tools run.
- **Writes nothing.** No files, caches or state are written. Archives are opened per call and
  nothing is kept between calls.
- **No credentials, no shell commands, no background work.**
- **Content is third-party text.** Article text from a ZIM is returned to the model as written by
  whoever made the archive. Scripts and styles are stripped and HTML is reduced to plain text, but
  the words themselves are not filtered. Use archives from sources you trust, such as Kiwix's own
  library.
- **Dependency licence.** `libzim` is GPL-3.0-or-later. It is installed as a separate package and
  is not bundled here. This plugin is MIT.

## Limits

- Text only: images, audio and video inside a ZIM are not returned (`zim_read` says so for a media
  entry).
- Split archives (`.zimaa`, `.zimab`, …) are not discovered. Point `paths` at a single `.zim` file.
- Full-text search needs the archive's own index. Kiwix's published files include one. A file
  without one is searched by title only, and `zim_list_books` shows `full_text_search: false`.
- Up to 50 in-book links are listed per article.

## Development

```bash
python -m pip install "libzim>=4.0.0,<5" pytest
python -m pytest -c tests/pytest.ini tests
OFFLINE_ZIM_REAL=/path/to/some.zim python -m pytest -c tests/pytest.ini tests -k real_world -s
hermes plugins validate --install-deps .
```

The tests build real ZIM archives with libzim's own writer: one with a full-text index, one with
titles only, and one file that is not a ZIM. `OFFLINE_ZIM_REAL` adds a smoke test against a
downloaded archive.

## License

MIT
