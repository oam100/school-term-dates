#!/usr/bin/env python3
"""Convert a term_dates.json into an RFC 5545 .ics file.

Reads the {label, holidays, terms, inset_days} JSON produced by a school's
term_dates.py and emits one VEVENT per entry. If the JSON has a "label"
(e.g. "Knole"), every SUMMARY is prefixed with "{label}: " so events from
different schools' feeds stay distinguishable if ever viewed together:
- holidays[]   -> one continuous all-day VEVENT per gap
- terms[]      -> two single-day VEVENTs per term: "First day back" (start)
                  and "Last day of term" (end) -- not a multi-day span, so
                  the calendar isn't filled solid for 6+ weeks at a time
- inset_days[] -> one single-day VEVENT per date

UIDs are derived from category + date (not random) so re-running this
script against a re-scraped JSON with unchanged dates produces identical
UIDs, and calendar apps that dedupe by UID update existing events rather
than duplicating them.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone

PRODID = "-//oli//school-term-dates//EN"


def load_term_dates(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    for key in ("holidays", "terms", "inset_days"):
        if key not in data:
            raise ValueError(f"{path} is missing required key {key!r}")
    return data


def format_all_day_range(start: date, end_inclusive: date) -> tuple[str, str]:
    """DTSTART/DTEND values (YYYYMMDD) for an all-day VEVENT.

    Per RFC 5545 3.6.1, DTEND for a DATE-value all-day event is exclusive,
    so an inclusive end date must be pushed forward one day.
    """
    dtend_exclusive = end_inclusive + timedelta(days=1)
    return start.strftime("%Y%m%d"), dtend_exclusive.strftime("%Y%m%d")


def build_vevent(uid: str, summary: str, start: date, end_inclusive: date, dtstamp: str) -> str:
    dtstart, dtend = format_all_day_range(start, end_inclusive)
    return "\r\n".join([
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{dtstart}",
        f"DTEND;VALUE=DATE:{dtend}",
        f"SUMMARY:{summary}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ])


def build_events(data: dict, dtstamp: str) -> list[tuple[date, str]]:
    """Returns a list of (sort_key_date, VEVENT text) pairs."""
    events = []

    label = data.get("label")
    prefix = f"{label}: " if label else ""

    for holiday in data["holidays"]:
        start = date.fromisoformat(holiday["start"])
        end = date.fromisoformat(holiday["end"])
        uid = f"holiday-{start.isoformat()}@school-term-dates"
        events.append((start, build_vevent(uid, f"{prefix}School Holiday", start, end, dtstamp)))

    for term in data["terms"]:
        start = date.fromisoformat(term["start"])
        end = date.fromisoformat(term["end"])
        start_uid = f"term-start-{start.isoformat()}@school-term-dates"
        end_uid = f"term-end-{end.isoformat()}@school-term-dates"
        events.append((start, build_vevent(start_uid, f"{prefix}First day back", start, start, dtstamp)))
        events.append((end, build_vevent(end_uid, f"{prefix}Last day of term", end, end, dtstamp)))

    for inset in data["inset_days"]:
        d = date.fromisoformat(inset["date"])
        uid = f"inset-{d.isoformat()}@school-term-dates"
        events.append((d, build_vevent(uid, f"{prefix}INSET Day (school closed)", d, d, dtstamp)))

    events.sort(key=lambda pair: pair[0])
    return events


def build_calendar(events: list[tuple[date, str]]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    lines.extend(vevent for _, vevent in events)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", default="term_dates.json")
    parser.add_argument("-o", "--output", default="term_dates.ics")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    data = load_term_dates(args.input)
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events = build_events(data, dtstamp)
    ics_text = build_calendar(events)

    with open(args.output, "w", newline="") as f:
        f.write(ics_text)

    if not args.quiet:
        print(f"Wrote {len(events)} events to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
