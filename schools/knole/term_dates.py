#!/usr/bin/env python3
"""Scrape school term dates from Knole Academy's TablePress-based term dates page.

Produces JSON via common.write_term_dates_json:
- holidays: date-range blocks for the gaps between terms
- terms: date-range blocks for each term (first/last day back)
- inset_days: every individual staff-development/INSET date, each as its
  own entry even when it also falls inside a holiday block
"""

import argparse
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"))
import common

DEFAULT_URL = "https://www.knoleacademy.org/our-school/term-dates/"
LABEL = "Knole"

ANNOTATION_RE = re.compile(r"\s*\([^)]*\)\s*$")
DATE_RE = re.compile(
    r"^\s*[A-Za-z]+\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\s*$"
)


class TablePressParser(HTMLParser):
    """Extracts rows of cell text from every <table id="tablepress-..."> on the page.

    Within a cell, <br> tags are turned into "\n" so multi-date cells
    (e.g. several staff-dev dates in one column) can be split back out later.
    """

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_tbody = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table" and (attrs_dict.get("id") or "").startswith("tablepress-"):
            self._in_table = True
            self._current_table = []
        elif tag == "tbody" and self._in_table:
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._current_row = []
        elif tag == "td" and self._current_row is not None:
            self._current_cell = []
        elif tag == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_data(self, data):
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._current_cell is not None:
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "table" and self._in_table:
            self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False


def parse_date(raw: str):
    cleaned = ANNOTATION_RE.sub("", raw).strip()
    match = DATE_RE.match(cleaned)
    if not match:
        raise ValueError(f"Could not parse date: {raw!r}")
    day, month_name, year = match.groups()
    try:
        from datetime import date, datetime

        month = datetime.strptime(month_name.capitalize(), "%B").month
        return date(int(year), month, int(day))
    except ValueError as e:
        raise ValueError(f"Could not parse date: {raw!r} ({e})")


def split_cell(cell: str) -> list[str]:
    return [part.strip() for part in cell.split("\n") if part.strip()]


def extract_terms(tables: list[list[list[str]]]) -> list[common.Term]:
    terms = []
    for table in tables:
        for row in table:
            if len(row) < 4:
                continue
            number, start_raw, end_raw, staffdev_raw = row[0], row[1], row[2], row[3]
            if not start_raw or not end_raw:
                continue
            start = parse_date(start_raw)
            end = parse_date(end_raw)
            staffdev_dates = [parse_date(d) for d in split_cell(staffdev_raw)]
            terms.append(common.Term(number=number, start=start, end=end, staffdev_dates=staffdev_dates))
    return terms


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="term dates page URL (supports file:// too)")
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="output JSON path (default: term_dates.json next to this script)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the stdout summary")
    args = parser.parse_args(argv)

    output_path = args.output
    if output_path is None:
        import pathlib

        output_path = str(pathlib.Path(__file__).parent / "term_dates.json")

    html = common.fetch_html(args.url)

    html_parser = TablePressParser()
    html_parser.feed(html)

    if not html_parser.tables:
        print("Error: no tablepress tables found on page — site structure may have changed", file=sys.stderr)
        return 2

    terms = extract_terms(html_parser.tables)

    result = common.write_term_dates_json(terms, output_path, label=LABEL)

    if not args.quiet:
        print(f"Parsed {len(terms)} terms from {len(html_parser.tables)} table(s)")
        print(f"  {len(result['holidays'])} holiday blocks")
        print(f"  {len(result['terms'])} term blocks")
        print(f"  {len(result['inset_days'])} inset days")
        print(f"Wrote {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
