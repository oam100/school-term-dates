#!/usr/bin/env python3
"""Shared, site-agnostic logic for school term-dates scrapers.

Each school's own term_dates.py handles fetching + parsing its specific
site's HTML into a list of Term objects, then hands off to this module to
compute holiday gaps and write the common {holidays, terms, inset_days}
JSON shape.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta


def fetch_html(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "term-dates-scraper/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP error fetching {url}: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error fetching {url}: {e.reason}")
    except TimeoutError:
        raise SystemExit(f"Timed out fetching {url} after {timeout}s")


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
