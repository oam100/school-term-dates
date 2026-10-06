#!/usr/bin/env python3
"""Shared, site-agnostic logic for school term-dates scrapers.

Each school's own term_dates.py handles fetching + parsing its specific
site's HTML into a list of Term objects, then hands off to this module to
compute holiday gaps and write the common {holidays, terms, inset_days}
JSON shape.
"""

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta


class FetchError(Exception):
    """A page couldn't be fetched (404, network error, timeout, ...)."""


def warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "term-dates-scraper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP error fetching {url}: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise FetchError(f"Network error fetching {url}: {e.reason}")
    except TimeoutError:
        raise FetchError(f"Timed out fetching {url} after {timeout}s")


@dataclass
class Term:
    number: str
    start: date
    end: date
    staffdev_dates: list[date] = field(default_factory=list)


def build_holidays(terms: list[Term]) -> list[dict]:
    holidays = []
    for i in range(len(terms) - 1):
        gap_start = terms[i].end + timedelta(days=1)
        gap_end = terms[i + 1].start - timedelta(days=1)
        if gap_start <= gap_end:
            holidays.append({"start": gap_start.isoformat(), "end": gap_end.isoformat()})
    return holidays


def build_terms_output(terms: list[Term]) -> list[dict]:
    return [{"start": t.start.isoformat(), "end": t.end.isoformat()} for t in terms]


def build_inset_days(terms: list[Term]) -> list[dict]:
    """Every staff-development/INSET date, deduped, as its own entry.

    Included even when the date also falls inside a holiday gap (see
    build_holidays) — inset days get their own calendar block rather than
    being silently absorbed into the holiday's date range.
    """
    seen = set()
    inset_days = []
    for term in terms:
        for d in term.staffdev_dates:
            if d not in seen:
                seen.add(d)
                inset_days.append(d)
    inset_days.sort()
    return [{"date": d.isoformat()} for d in inset_days]


def write_term_dates_json(terms: list[Term], output_path: str, label: str | None = None) -> dict:
    """Write the {label, holidays, terms, inset_days} JSON shape.

    label is a short school identifier (e.g. "Knole") that ics_export.py
    prefixes onto every event SUMMARY, so events from multiple schools'
    feeds stay distinguishable if they're ever viewed side by side.
    """
    terms = sorted(terms, key=lambda t: t.start)

    result = {
        "label": label,
        "holidays": build_holidays(terms),
        "terms": build_terms_output(terms),
        "inset_days": build_inset_days(terms),
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def load_previous_terms(output_path: str) -> list[Term]:
    """Rebuild Terms from a previously written term_dates.json, if any.

    The JSON only stores inset days as a flat list, so each one is attached
    to the latest term starting on or before it (or the first term) -- the
    attachment doesn't matter beyond carrying it through merge_previous_terms.
    """
    try:
        with open(output_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    terms = sorted(
        (Term(number="", start=date.fromisoformat(t["start"]), end=date.fromisoformat(t["end"]))
         for t in data.get("terms", [])),
        key=lambda t: t.start,
    )
    if not terms:
        return []
    for entry in data.get("inset_days", []):
        d = date.fromisoformat(entry["date"])
        owner = next((t for t in reversed(terms) if t.start <= d), terms[0])
        owner.staffdev_dates.append(d)
    return terms


def merge_previous_terms(scraped: list[Term], output_path: str) -> list[Term]:
    """Keep terms from the previous output that predate everything scraped.

    Schools take old academic years' pages down, so without this a past
    year would vanish from subscribers' calendars the moment its page goes.
    Previous terms are only kept when they end before the first scraped
    term starts, so the live site always wins wherever it has data; inset
    days are only kept up to the last kept term's end, so ones falling in
    the gap before the first scraped term come from the site too.
    """
    previous = load_previous_terms(output_path)
    if not scraped:
        return previous
    first_scraped_start = min(t.start for t in scraped)
    archived = [t for t in previous if t.end < first_scraped_start]
    if archived:
        cutoff = archived[-1].end
        for t in archived:
            t.staffdev_dates = [d for d in t.staffdev_dates if d <= cutoff]
    return archived + scraped


def check_coverage(terms: list[Term], min_months: int, today: date | None = None) -> None:
    """Fail unless the term data reaches at least min_months ahead of today."""
    today = today or date.today()
    horizon = add_months(today, min_months)
    covered_until = max((t.end for t in terms), default=None)
    if covered_until is None or covered_until < horizon:
        raise SystemExit(
            f"Error: term dates only cover up to {covered_until or 'nothing'}, "
            f"need at least {min_months} months ahead (to {horizon})"
        )


def finalize_term_dates(
    scraped: list[Term], output_path: str, label: str | None = None, min_months: int = 9
) -> dict:
    """Merge with previous output, check coverage, then write the JSON.

    This is the single pass/fail point for a scraper: individual pages may
    be missing or unparseable (and are just warned about), but if the
    combined data doesn't reach min_months ahead, nothing is written and
    the script exits non-zero so the last known-good feed stays published.
    """
    terms = merge_previous_terms(scraped, output_path)
    check_coverage(terms, min_months)
    return write_term_dates_json(terms, output_path, label=label)
