# Halalan 2028 Tracker

A single-page web app for Filipino voters ahead of the May 8, 2028 national and local elections. Owner: Pat Quitoriano.

## What's here
- `index.html` — the page (HTML + CSS + JS, no build step). Google Fonts is the only external dependency. It fetches `data.json`, `news.json` and `photos.json`, so preview it through a server (`python3 -m http.server`).
- `data.json` — curated facts, edited by hand: `people` (id → name, Wikipedia title), `aspirants` (pres / vp / sen, each `{id, role, status, note, date, src: [source ids]}`), `voterCounts`, `overseas`, `notes`, `surveys` (`pollster, fieldwork, n, moe, src, rows`), `timeline`, `sources` (id → [title, url]).
- `news.json`, `photos.json` — written by `scraper/scrape.py`; do not hand-edit.
- Four views routed by URL hash: `#botante` (eligibility checker, registration, voter counts), `#kandidato` (status overview, aspirants, surveys, profiles), `#halalan` (timeline, practice ballot, rules, sources), `#balita` (news feed).
- `scraper/validate.py` must pass before publishing; it warns on entries with no source.

## Data rules
- Data compiled Nov 10, 2026. COMELEC has **no public API** for voter counts; figures come from COMELEC press statements and news reports.
- Aspirant `status` values: `ok` = publicly declared, `warn` = said open/considering or named as likely, `vio` = appears in surveys or floated in news with no stated plan, `no` = ruled out or legally barred (term limit, age).
- Nobody is an official candidate until COC filing (planned Sept 20–27, 2027).
- Keep it politically neutral: report what people said and what surveys found, with pollster, fieldwork dates, sample size and margin of error. No predictions or endorsements.
- Every figure needs a source in `DATA.sources`.

## Key facts as of Nov 10, 2026
- Registration: Nov 10, 2026 – Jun 30, 2027 (per COMELEC on opening day; earlier reports cited later cut-offs).
- Registered regular voters: ~71M (May 2026). 2025 polls: 68,618,667. COMELEC projects ~74M for 2028.
- Only declared presidential candidate: Sara Duterte (Feb 18, 2026). Her Senate impeachment trial is ongoing; a verdict was floated for December 2026 with no fixed date.
- No declared VP candidate yet.

## Automation (done)
- `.github/workflows/daily-update.yml` runs daily at 06:17 PHT: validate → scrape → commit `news.json`/`photos.json` → open a `needs-review` issue when an article may change a fact.
- The scraper never edits `data.json`. Status, count and survey changes are applied by a person after reading the source.

## Open items
- Find sources for Jonvic Remulla, Baste Duterte (Tangere scenario) and Isko Moreno entries (validator warnings).
- COMELEC's own site is not in the feed list; add its press-release RSS if one becomes available.
