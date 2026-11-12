# Halalan 2028 Tracker

A static site for Filipino voters ahead of the May 8, 2028 elections, plus a daily
GitHub Actions job that keeps its news feed current.

## Files

| File | What it is | Who edits it |
|---|---|---|
| `index.html` | The whole site (HTML, CSS, JS). Reads the three JSON files below. | You |
| `data.json` | Curated facts: voter counts, aspirants and statuses, surveys, timeline, sources. | **You, by hand** |
| `news.json` | Headlines about the 2028 elections, each linked to the article. | The daily job |
| `photos.json` | Freely licensed Wikimedia Commons photos, with author and licence. | The daily job |
| `api/poll.js` | Write-in visitor poll for President and VP (Vercel function, Upstash Redis). | |
| `namematch.js` | Matches typed names to people, forgiving typos and nicknames. Used by the page and the API. | |
| `scraper/scrape.py` | The scraper (Python standard library only). | |
| `scraper/config.json` | Feeds, keywords and the rules for flagging articles. | You |
| `scraper/validate.py` | Checks `data.json` (statuses, source ids, survey fields). | |
| `.github/workflows/daily-update.yml` | Runs everything every day at 06:17 Philippine time. | |

## How the daily update works

1. Reads Google News searches plus Inquirer, GMA, Philstar, Rappler and SunStar RSS feeds.
2. Keeps articles about 2028, COMELEC, registration, surveys or the impeachment trial,
   tags them by topic and by aspirant, and commits them to `news.json`. They appear on the
   **Balita** tab and in each person's profile right away.
3. If an article suggests a fact in `data.json` may be out of date (someone declares or
   withdraws, a new voter count, a new survey, a registration-deadline change, an impeachment
   verdict) it **opens a GitHub issue labelled `needs-review`** listing each article.
   `data.json` is never changed automatically: you check the article, edit `data.json`,
   add the article to `sources`, and push.
4. Looks up photos on Wikipedia, keeping only Commons-hosted (freely licensed) images.

## Setup (one time)

1. Create a GitHub repository and push this folder to it.
2. **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/ (root)`.
3. **Settings → Actions → General → Workflow permissions**: "Read and write permissions".
4. **Actions → Daily data update → Run workflow** to run it once now.

## Preview locally

The page loads JSON with `fetch`, so open it through a local server, not by double-clicking:

```bash
python3 -m http.server 8000
```

Then open http://localhost:8000.

## Test the scraper offline

```bash
python3 scraper/scrape.py --root /tmp/h28test --fixtures scraper/fixtures --today 2026-11-10
```

(copy `data.json` into `/tmp/h28test` first).

## Official ballot and visitor poll

The **practice ballot** uses placeholder names until COMELEC releases the official list of
candidates. Then, in `data.json`:

1. Fill `ballot.positions` with the national posts in ballot order:
   `{"job": "pres", "max": 1, "candidates": [{"no": 1, "name": "...", "party": "..."}]}`
   (`job` matches an id in `jobs`; `sen` has `max: 12`, `pl` has `max: 1`).
2. Set `ballot.released` (YYYY-MM-DD), add COMELEC's list to `sources` and to `ballot.src`,
   and set `ballot.official` to `true`. Run `python3 scraper/validate.py`.

The **visitor poll** (*Botohan ng mga bisita*) asks who visitors want for President and Vice
President. They type a name; `namematch.js` matches it to a known person even with typos,
swapped words, titles or nicknames ("Sarah Dturete", "Inday Sara", "VP Sara" → Sara Duterte).
When a name is unclear (just "Duterte" or "Tulfo") it asks *Ibig mo bang sabihin…* with the
options. Names it does not know count under **Iba pa**. It is opt-in and labelled
*Hindi ito survey*: it never appears with the surveys, and results show only after voting.

Setup (one time):

1. In Vercel: **Storage → Marketplace → Upstash Redis**, connect it to the project
   (this sets `KV_REST_API_URL` and `KV_REST_API_TOKEN`). Add an env var `POLL_SALT` with a long random string.
2. `poll.open` is `true` in `data.json`. Set `poll.closes` (YYYY-MM-DD) to stop it, or `open: false`.

Reviewing "Iba pa": unknown names are tallied in Redis hashes `poll:other:pres` and
`poll:other:vp` (Upstash console → Data Browser). If one is a nickname of someone in `people`,
add it to `poll.aliases`; if it is a new person, add `{"id", "name", "aliases"}` to `poll.extra`.

The poll stores only tallies, a random device cookie and an hourly per-network counter
(a salted hash of the IP, kept for one hour). It limits repeat voting but cannot stop a
determined person, which is one more reason it is not a survey.
