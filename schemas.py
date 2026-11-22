"""Tool schemas the model reads."""

ZIM_LIST_BOOKS = {
    "name": "zim_list_books",
    "description": (
        "List the offline ZIM libraries the user has on this machine (for example an offline "
        "Wikipedia, Wiktionary, Stack Exchange site or Project Gutenberg). Returns each book's id, "
        "title, description, language, date, article count, whether it has a full-text index, and "
        "its main page path. Works without internet access."
    ),
    "parameters": {"type": "object", "properties": {}},
}

ZIM_SEARCH = {
    "name": "zim_search",
    "description": (
        "Search the user's offline ZIM libraries. Uses each book's full-text index when it has "
        "one (results carry a text snippet) and falls back to matching article titles when it "
        "does not. Returns the book id, article path and title of each match; pass them to "
        "zim_read to read the article."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to search for."},
            "book": {
                "type": "string",
                "description": "Search only this book id (from zim_list_books). Default: every configured book.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results (1-25, default 10).",
                "minimum": 1,
                "maximum": 25,
            },
        },
        "required": ["query"],
    },
}

ZIM_READ = {
    "name": "zim_read",
    "description": (
        "Read one article from an offline ZIM library as plain text (headings marked with #, list "
        "items with -). Long articles come back in parts: pass the returned next_offset as "
        "offset to read on. The first part also lists the article's links to other articles in "
        "the same book, as paths zim_read accepts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "book": {"type": "string", "description": "Book id from zim_list_books or zim_search."},
            "path": {
                "type": "string",
                "description": "Article path from zim_search or a link (an exact article title also works).",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset to start at (default 0).",
                "minimum": 0,
            },
        },
        "required": ["book", "path"],
    },
}
