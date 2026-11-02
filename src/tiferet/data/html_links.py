"""Links of an HTML page, for adapters that find their data file on a page each run."""

from html.parser import HTMLParser


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


def page_links(page: bytes) -> list[str]:
    """The `href` of every `<a>` element, with character references resolved."""
    parser = _Links()
    parser.feed(page.decode("utf-8", errors="replace"))
    parser.close()
    return parser.hrefs
