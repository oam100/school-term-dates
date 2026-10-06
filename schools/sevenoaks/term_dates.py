#!/usr/bin/env python3
"""Scrape school term dates from Sevenoaks Primary School's term-dates pages.

Unlike Knole's TablePress table (one row per term), Sevenoaks' page is a
plain hand-authored <table> with one row per field ("Term Starts", "Term
Ends", "Staff Development Days"), grouped under a "TERM N - YEAR" header
row. The year only appears in that header, never in the date cells
themselves, so it must be tracked as context while walking rows. Each
academic year lives on its own page, so multiple URLs are scraped and their
terms merged into one sorted list before computing holiday gaps -- this is
what makes the summer gap between academic years show up as a single
continuous holiday block instead of two disjoint ones.

The year pages are discovered by URL pattern rather than hardcoded: every
academic year from last year to YEARS_AHEAD ahead is tried, and pages that
don't exist (not yet published, or already taken down) are skipped.

Produces JSON via common.write_term_dates_json:
- holidays: date-range blocks for the gaps between terms
- terms: date-range blocks for each term (first/last day back)
- inset_days: every individual staff-development/INSET date, each as its
  own entry even when it also falls inside a holiday block

"Bank Holiday" rows are deliberately skipped: they're well-known public
holidays, redundant with a school term-dates calendar.
"""

import argparse
import re
import sys
from datetime import date, datetime
from html.parser import HTMLParser

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "scripts"))
import common

URL_TEMPLATE = "https://www.sevenoaksprimary.co.uk/Parents/Term-Dates-{start}-{end}/"
YEARS_AHEAD = 2
LABEL = "S’oaks"

TERM_HEADER_RE = re.compile(r"TERM\s*\d+\s*[-–—]\s*(\d{4})", re.I)
ANNOTATION_RE = re.compile(r"\s*\([^)]*\)\s*$")
DATE_RE = re.compile(r"^(?:[A-Za-z]+\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s*$")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


class LabeledRowTableParser(HTMLParser):
    """Extracts rows of cells from every <table> on the page.

    Each cell is a list of paragraph strings: <p> and <br> both act as
    paragraph separators, so a "Staff Development Days" cell holding
    several dates (one per <p>) comes back as one string per date. Loose
    text sitting outside any <p> (seen once in the source, a stray "NONE")
    is still captured as its own paragraph when the cell closes.
    """

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[list[str]]]] = []
        self._in_table = False
        self._current_table: list[list[list[str]]] = []
        self._current_row: list[list[str]] | None = None
        self._current_cell: list[str] | None = None
        self._current_para: list[str] | None = None
        self._in_td = False

    def _flush_para(self):
        if self._current_para is None:
            return
        text = normalize_ws("".join(self._current_para))
        if text:
            self._current_cell.append(text)
        self._current_para = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag == "td" and self._current_row is not None:
            self._in_td = True
            self._current_cell = []
            self._current_para = []
        elif tag in ("p", "br") and self._in_td:
            self._flush_para()

    def handle_data(self, data):
        if self._in_td:
            self._current_para.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self._in_td:
            self._flush_para()
        elif tag == "td" and self._in_td:
            self._flush_para()
            self._current_row.append(self._current_cell)
            self._current_cell = None
            self._current_para = None
            self._in_td = False
        elif tag == "tr" and self._current_row is not None:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._in_table:
            self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False


def cell_text(cell: list[str]) -> str:
    return " ".join(cell).strip()


def parse_date_with_year(raw: str, year: int | None) -> date:
    if year is None:
        raise ValueError(f"No 'TERM N - YEAR' header seen yet before date: {raw!r}")
    cleaned = ANNOTATION_RE.sub("", normalize_ws(raw))
    match = DATE_RE.match(cleaned)
    if not match:
        raise ValueError(f"Could not parse date: {raw!r}")
    day, month_name = match.groups()
    try:
        month = datetime.strptime(month_name.capitalize(), "%B").month
    except ValueError as e:
        raise ValueError(f"Could not parse date: {raw!r} ({e})")
    return date(year, month, int(day))


def extract_terms(tables: list[list[list[list[str]]]]) -> list[common.Term]:
    terms = []
    for table in tables:
        current_year = None
        pending_start = None
        pending_end = None
        pending_staffdev: list[date] = []
        term_index = 0

        def flush_pending():
            nonlocal pending_start, pending_end, pending_staffdev, term_index
            if pending_start is not None and pending_end is not None:
                term_index += 1
                terms.append(
                    common.Term(
                        number=str(term_index),
                        start=pending_start,
                        end=pending_end,
                        staffdev_dates=pending_staffdev,
                    )
                )
            pending_start = None
            pending_end = None
            pending_staffdev = []

        for row in table:
            if len(row) < 3:
                continue
            col1 = cell_text(row[0])
            col2 = cell_text(row[1])
            col3 = row[2]

            header_match = TERM_HEADER_RE.search(col1)
            if header_match:
                flush_pending()
                current_year = int(header_match.group(1))

            col2_norm = col2.lower()
            if col2_norm == "term starts" and col3:
                pending_start = parse_date_with_year(col3[0], current_year)
            elif col2_norm == "term ends" and col3:
                pending_end = parse_date_with_year(col3[0], current_year)

            col1_norm = col1.lower()
            if "staff development day" in col1_norm:
                for date_text in col3:
                    if date_text.strip().upper() == "NONE":
                        continue
                    pending_staffdev.append(parse_date_with_year(date_text, current_year))
            # "Bank Holiday" rows are deliberately not captured.

        flush_pending()
    return terms


def candidate_urls(today: date | None = None) -> list[str]:
    """Year-page URLs from last academic year to YEARS_AHEAD years ahead."""
    today = today or date.today()
    current_start = today.year if today.month >= 9 else today.year - 1
    return [
        URL_TEMPLATE.format(start=y, end=y + 1)
        for y in range(current_start - 1, current_start + YEARS_AHEAD + 1)
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", nargs="*", default=None,
        help="one or more term-dates page URLs (default: discover academic-year pages around today)",
    )
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

    # Missing or unparseable pages are skipped with a warning; whether
    # enough data survived is decided by finalize_term_dates' coverage check.
    urls = args.url or candidate_urls()
    all_terms: list[common.Term] = []
    pages_used = 0
    total_tables = 0
    for url in urls:
        try:
            html = common.fetch_html(url)
        except common.FetchError as e:
            common.warn(f"skipping page: {e}")
            continue
        html_parser = LabeledRowTableParser()
        html_parser.feed(html)
        if not html_parser.tables:
            common.warn(f"skipping {url}: no tables found — site structure may have changed")
            continue
        try:
            terms = extract_terms(html_parser.tables)
        except ValueError as e:
            common.warn(f"skipping {url}: {e}")
            continue
        pages_used += 1
        total_tables += len(html_parser.tables)
        all_terms.extend(terms)

    result = common.finalize_term_dates(all_terms, output_path, label=LABEL, min_months=args.min_months)

    if not args.quiet:
        print(f"Parsed {len(all_terms)} terms from {pages_used} page(s), {total_tables} table(s)")
        print(f"  {len(result['holidays'])} holiday blocks")
        print(f"  {len(result['terms'])} term blocks")
        print(f"  {len(result['inset_days'])} inset days")
        print(f"Wrote {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
