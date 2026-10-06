# school-term-dates

Scrapes UK school term-dates pages into `.ics` calendar feeds that Google
Calendar (or any calendar app) can subscribe to via "Add calendar > From
URL". A GitHub Actions workflow re-scrapes and re-publishes weekly
(`.github/workflows/update-term-dates.yml`), so nothing needs to run on any
personal machine.

## Subscribe

Add one of these as a "From URL" calendar subscription:

- Knole Academy: `https://raw.githubusercontent.com/oam100/school-term-dates/main/schools/knole/term_dates.ics`
- Sevenoaks Primary: `https://raw.githubusercontent.com/oam100/school-term-dates/main/schools/sevenoaks/term_dates.ics`

Each feed contains, every SUMMARY prefixed with the school's label (e.g.
"Knole: ", "S’oaks: ") so events stay distinguishable if both feeds are ever
viewed in the same calendar:
- One continuous all-day event per holiday period ("School Holiday")
- Two single-day events per term: "First day back" and "Last day of term"
- Individual "INSET Day (school closed)" events for every staff-development
  date, each as its own event even when it also falls inside a holiday's
  date range (so a staff-training day right before half-term still shows
  up distinctly, not just silently absorbed into the holiday block)

## Layout

```
scripts/
  common.py       # shared: fetch_html, Term dataclass, holiday/term/inset builders
  ics_export.py   # generic term_dates.json -> term_dates.ics converter
schools/
  <school>/
    term_dates.py    # site-specific scraper: fetch + parse only
    term_dates.json  # generated, committed
    term_dates.ics   # generated, committed (what Google Calendar subscribes to)
```

## Adding a school

1. Create `schools/<name>/term_dates.py`. It only needs to fetch the
   site's HTML, parse it into a list of `common.Term(number, start, end,
   staffdev_dates)`, and call
   `common.write_term_dates_json(terms, output_path, label="Short Name")`
   — the label is what prefixes every event's SUMMARY in the ICS output.
   See `schools/knole/term_dates.py` (TablePress table) and
   `schools/sevenoaks/term_dates.py` (plain hand-authored table, multiple
   year-pages merged before output) for two different site shapes.
2. Nothing else needs to change — the Actions workflow loops over every
   `schools/*/` directory automatically.

## Manual regeneration

```
python schools/<name>/term_dates.py
python scripts/ics_export.py -i schools/<name>/term_dates.json -o schools/<name>/term_dates.ics
```

## Local git credential

If you want to push from a local machine (rather than waiting for the
weekly Action), authenticate with a fine-grained GitHub PAT scoped to just
this repo (Contents: Read and write):

```
gh auth login --with-token < ~/.config/school-term-dates/github-token
```

That token file lives outside this repo (`~/.config/...`) and must never be
committed anywhere — it's not covered by this repo's `.gitignore` since it
isn't inside the repo at all.

## Failure handling

Individual pages and tables are allowed to fail: a 404 (old academic-year
pages get taken down; future ones may not exist yet) or an unparseable
table is logged as a warning and skipped. Sevenoaks' year pages are found
by trying every academic year from last year to two years ahead, so new
years get picked up without code changes.

The one hard requirement is coverage: after scraping, a school's data must
reach at least 9 months from today (`--min-months` to change). If it
doesn't, nothing is written and the script exits non-zero, so the last
known-good feed stays published. The Actions workflow still refreshes and
commits every other school, then fails the run so the problem is visible.

Terms that have disappeared from the site (e.g. last year's page was
removed) are kept from the previously committed `term_dates.json`, so past
events don't vanish from subscribers' calendars. The live site always wins
for any period it covers.

A page changing in a way that still "parses" but produces wrong data
(e.g. reordered columns) isn't detected -- a quieter failure mode that's
out of scope here.

Sevenoaks Primary's "Bank Holiday" rows are deliberately not included in
the output — they're well-known public holidays, redundant with a
term-dates calendar.
