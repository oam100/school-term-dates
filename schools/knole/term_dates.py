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
from datetime import date, datetime
from html.parser import HTMLParser

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"))
import common

DEFAULT_URL = "https://www.knoleacademy.org/our-school/term-dates/"
LABEL = "Knole"

ANNOTATION_RE = re.compile(r"\s*\([^)]*\)\s*$")
# The year is optional: the site occasionally omits it (e.g. "Friday 5th
# November"), in which case parse_date infers it from a nearby date.
DATE_RE = re.compile(
    r"^\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)(?:\s+(\d{4}))?\s*$"
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


def parse_date(raw: str, near: date | None = None) -> date:
    """Parse e.g. "Friday 5th November 2027".

    When the year is missing, pick the year within one of `near`'s whose
    date falls on the stated weekday, closest to `near` -- so "Friday 5th
    November" in a 2027/28 term resolves to 2027.
    """
    cleaned = ANNOTATION_RE.sub("", raw).strip()
    match = DATE_RE.match(cleaned)
    if not match:
        raise ValueError(f"Could not parse date: {raw!r}")
    weekday_name, day, month_name, year = match.groups()
    try:
        month = datetime.strptime(month_name.capitalize(), "%B").month
        if year is not None:
            return date(int(year), month, int(day))
    except ValueError as e:
        raise ValueError(f"Could not parse date: {raw!r} ({e})")

    if near is None:
        raise ValueError(f"Date has no year and no nearby date to infer it from: {raw!r}")
    candidates = []
    for y in (near.year - 1, near.year, near.year + 1):
        try:
            d = date(y, month, int(day))
        except ValueError:
            continue
        if d.strftime("%A").lower() == weekday_name.lower():
            candidates.append(d)
    if not candidates:
        raise ValueError(f"Could not infer year for {raw!r}: weekday doesn't match any year near {near}")
    return min(candidates, key=lambda d: abs((d - near).days))


def split_cell(cell: str) -> list[str]:
    return [part.strip() for part in cell.split("\n") if part.strip()]


def extract_terms(table: list[list[str]]) -> list[common.Term]:
    terms = []
    for row in table:
        if len(row) < 4:
            continue
        number, start_raw, end_raw, staffdev_raw = row[0], row[1], row[2], row[3]
        if not start_raw or not end_raw:
            continue
        try:
            start = parse_date(start_raw)
            end = parse_date(end_raw, near=start)
        except ValueError:
            end = parse_date(end_raw)
            start = parse_date(start_raw, near=end)
        staffdev_dates = [parse_date(d, near=start) for d in split_cell(staffdev_raw)]
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
    parser.add_argument(
        "--min-months", type=int, default=9,
        help="fail unless term dates reach this many months ahead (default: 9)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the stdout summary")
    args = parser.parse_args(argv)

    output_path = args.output
    if output_path is None:
        import pathlib

        output_path = str(pathlib.Path(__file__).parent / "term_dates.json")

    # Each academic year is its own table. A table that fails to parse is
    # skipped with a warning rather than failing the run; whether enough
    # data survived is decided by finalize_term_dates' coverage check.
    terms: list[common.Term] = []
    tables: list[list[list[str]]] = []
    try:
        html_parser = TablePressParser()
        html_parser.feed(common.fetch_html(args.url))
        tables = html_parser.tables
        if not tables:
            common.warn(f"no tablepress tables found on {args.url} — site structure may have changed")
    except common.FetchError as e:
        common.warn(str(e))
    for i, table in enumerate(tables, 1):
        try:
            terms.extend(extract_terms(table))
        except ValueError as e:
            common.warn(f"skipping table {i}: {e}")

    result = common.finalize_term_dates(terms, output_path, label=LABEL, min_months=args.min_months)

    if not args.quiet:
        print(f"Parsed {len(terms)} terms from {len(tables)} table(s)")
        print(f"  {len(result['holidays'])} holiday blocks")
        print(f"  {len(result['terms'])} term blocks")
        print(f"  {len(result['inset_days'])} inset days")
        print(f"Wrote {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
