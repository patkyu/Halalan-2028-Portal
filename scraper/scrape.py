#!/usr/bin/env python3
"""Daily scraper for the Halalan 2028 Tracker.

What it does, in order:
  1. Reads the news feeds listed in scraper/config.json (Google News searches
     plus publisher RSS feeds).
  2. Keeps items about the 2028 elections, tags them by topic and by the
     aspirants they mention, and merges them into news.json. The site shows
     these as a news feed, each item linked to its original article.
  3. Looks for signals that a curated fact in data.json may be out of date
     (someone declares or withdraws, a new voter count, a new survey, a change
     to the registration deadline, an impeachment verdict). These are written
     to review.json / review.md for a human to check. The scraper never edits
     data.json: statuses, counts and survey numbers change only by hand.
  4. Looks up a freely licensed photo for each person on Wikipedia/Wikimedia
     Commons and records it, with author and licence, in photos.json.

Standard library only, so the workflow needs no pip install.

Usage:
  python scraper/scrape.py                    # normal run (network)
  python scraper/scrape.py --fixtures DIR     # read feeds from DIR/<feed-id>.xml
  python scraper/scrape.py --no-photos        # skip the Wikipedia lookups
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))
UA = CONFIG.get("userAgent", "Halalan2028TrackerBot/1.0")
STATUS_LABEL = {"ok": "Kumpirmado (declared)", "warn": "Naririnig (open/considering)",
                "vio": "Gusto ng bayan (in surveys, no plan)", "no": "Hindi tatakbo (ruled out/barred)"}
POS_LABEL = {"pres": "President", "vp": "Vice President", "sen": "Senator"}


# ---------------------------------------------------------------- helpers
def log(*a):
    print(*a, file=sys.stderr)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def http_get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def has_term(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match (text must already be lower-cased)."""
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip()
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        pass
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


# ---------------------------------------------------------------- feeds
def feed_url(feed: dict) -> str:
    if feed["type"] == "gnews":
        q = urllib.parse.quote(feed["q"] + " when:14d")
        return f"https://news.google.com/rss/search?q={q}&hl=en-PH&gl=PH&ceid=PH:en"
    return feed["url"]


def parse_feed(raw: bytes, feed: dict) -> list[dict]:
    root = ET.fromstring(raw)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == atom + "feed":
        for e in root.findall(atom + "entry"):
            link = e.find(atom + "link")
            out.append({
                "title": strip_html(e.findtext(atom + "title", "")),
                "url": link.get("href") if link is not None else "",
                "date": parse_date(e.findtext(atom + "updated") or e.findtext(atom + "published")),
                "summary": strip_html(e.findtext(atom + "summary", "")),
                "publisher": "",
            })
        return out
    for it in root.iter("item"):
        src = it.find("source")
        title = strip_html(it.findtext("title", ""))
        publisher = (src.text or "").strip() if src is not None else ""
        if feed["type"] == "gnews" and publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        out.append({
            "title": title,
            "url": (it.findtext("link") or "").strip(),
            "date": parse_date(it.findtext("pubDate")),
            "summary": strip_html(it.findtext("description", "")),
            "publisher": publisher,
        })
    return out


def publisher_from_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    known = {"inquirer.net": "Inquirer", "newsinfo.inquirer.net": "Inquirer", "gmanetwork.com": "GMA News",
             "data.gmanetwork.com": "GMA News", "philstar.com": "Philstar", "rappler.com": "Rappler",
             "sunstar.com.ph": "SunStar", "tribune.net.ph": "Daily Tribune", "abs-cbn.com": "ABS-CBN News",
             "news.abs-cbn.com": "ABS-CBN News", "mb.com.ph": "Manila Bulletin", "pna.gov.ph": "PNA",
             "comelec.gov.ph": "COMELEC"}
    return known.get(host, host)


# ---------------------------------------------------------------- tagging
def build_people_index(data: dict) -> list[tuple[str, list[str]]]:
    """[(person_id, [lower-case names/aliases])]. Full names only, to avoid
    matching a bare surname shared by relatives (Tulfo, Duterte, Marcos)."""
    idx = []
    for pid, p in data["people"].items():
        names = {p["name"].lower()} | {a.lower() for a in p.get("aliases", [])}
        idx.append((pid, sorted(names)))
    return idx


def tag_item(text: str, people_idx) -> tuple[list[str], list[str]]:
    topics = [t for t, words in CONFIG["topics"].items() if any(has_term(text, w) for w in words)]
    people = [pid for pid, names in people_idx if any(has_term(text, n) for n in names)]
    return topics, people


def statuses_of(data: dict, pid: str) -> list[str]:
    out = []
    for pos, lst in data["aspirants"].items():
        for a in lst:
            if a["id"] == pid:
                out.append(f"{POS_LABEL[pos]}: {STATUS_LABEL[a['status']]}")
    return out or ["not on the tracker yet"]


def flags_for(item: dict, text: str, data: dict) -> list[dict]:
    """Rules that say 'a human should look at data.json because of this article'."""
    flags = []
    link = {"title": item["title"], "url": item["url"], "publisher": item["publisher"], "date": item["date"]}

    if "candidacy" in item["topics"] and item["people"] and any(has_term(text, s) for s in CONFIG["candidacySignals"]):
        for pid in item["people"]:
            flags.append({"kind": "status", "person": pid, "name": data["people"][pid]["name"],
                          "current": statuses_of(data, pid), "article": link,
                          "ask": "Does this article report a new declaration, withdrawal or change of plan?"})

    latest = [v for v in data["voterCounts"] if v["type"] == "count"][-1]["value"]
    for m in re.finditer(r"(\d{2}(?:\.\d{1,2})?)\s*(?:million|m)\s+(?:registered\s+|new\s+|qualified\s+)?voters", text):
        val = float(m.group(1))
        if 50 <= val <= 90 and abs(val - latest) >= 0.05:
            flags.append({"kind": "voterCount", "value": val, "current": latest, "article": link,
                          "ask": f"Article mentions {val}M voters (tracker shows {latest}M). Is this a new COMELEC count, a projection, or an old figure?"})
            break

    if "survey" in item["topics"]:
        for p in CONFIG["pollsters"]:
            if has_term(text, p) and ("2028" in text or "senat" in text or "presiden" in text):
                known = {s[1] for s in data["sources"].values()}
                if item["url"] not in known:
                    flags.append({"kind": "survey", "pollster": p, "article": link,
                                  "ask": "New survey release? If so add it to data.json → surveys with fieldwork dates, n and margin of error."})
                break

    if "registration" in item["topics"] and any(has_term(text, s) for s in CONFIG["registrationSignals"]):
        flags.append({"kind": "registration", "current": data["regEnd"], "article": link,
                      "ask": f"Any change to the registration window? Tracker shows {data['regStart']} to {data['regEnd']}."})

    if "impeachment" in item["topics"] and any(has_term(text, s) for s in CONFIG["impeachmentSignals"]):
        flags.append({"kind": "impeachment", "article": link,
                      "ask": "Did the Senate reach a verdict or set a date? Update notes, timeline and Sara Duterte's entry if so."})
    return flags


# ---------------------------------------------------------------- photos
def wiki_api(host: str, params: dict) -> dict:
    params = {**params, "format": "json", "formatversion": "2"}
    return json.loads(http_get(f"https://{host}/w/api.php?" + urllib.parse.urlencode(params)))


def refresh_photos(data: dict, photos: dict, today: dt.date) -> int:
    """Fill photos.json with Commons-hosted (freely licensed) lead images.
    Images hosted only on English Wikipedia are usually non-free and skipped."""
    want = {}
    for pid, p in data["people"].items():
        have = photos.get(pid)
        stale = not have or (today - dt.date.fromisoformat(have.get("checked", "2000-01-01"))).days > 30
        if p.get("wiki") and stale:
            want[p["wiki"]] = pid
    if not want:
        return 0
    titles = list(want)
    found = 0
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        try:
            r = wiki_api("en.wikipedia.org", {"action": "query", "prop": "pageimages|info", "piprop": "thumbnail|name",
                                              "pithumbsize": "240", "inprop": "url", "redirects": "1", "titles": "|".join(chunk)})
        except Exception as e:  # noqa: BLE001
            log("photos: wikipedia lookup failed:", e)
            return found
        q = r.get("query", {})
        back = {t: t for t in chunk}
        for rd in q.get("normalized", []) + q.get("redirects", []):
            back[rd["to"]] = back.get(rd["from"], rd["from"])
        pages = [pg for pg in q.get("pages", []) if pg.get("pageimage") and pg.get("thumbnail")]
        if not pages:
            continue
        files = ["File:" + pg["pageimage"] for pg in pages]
        try:
            c = wiki_api("commons.wikimedia.org", {"action": "query", "prop": "imageinfo", "iiprop": "extmetadata|url",
                                                   "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                                                   "titles": "|".join(files)})
        except Exception as e:  # noqa: BLE001
            log("photos: commons lookup failed:", e)
            return found
        meta = {}
        for pg in c.get("query", {}).get("pages", []):
            if pg.get("missing") or not pg.get("imageinfo"):
                continue  # not on Commons -> likely non-free; skip
            md = pg["imageinfo"][0].get("extmetadata", {})
            meta[pg["title"].removeprefix("File:").replace(" ", "_")] = {
                "artist": strip_html(md.get("Artist", {}).get("value", ""))[:120],
                "license": md.get("LicenseShortName", {}).get("value", ""),
                "licenseUrl": md.get("LicenseUrl", {}).get("value", ""),
                "filePage": pg["imageinfo"][0].get("descriptionurl", ""),
            }
        for pg in pages:
            m = meta.get(pg["pageimage"].replace(" ", "_"))
            pid = want.get(back.get(pg["title"], pg["title"]))
            if not pid:
                continue
            if m:
                photos[pid] = {"src": pg["thumbnail"]["source"], "page": pg.get("fullurl", ""), **m,
                               "checked": today.isoformat()}
                found += 1
            else:
                photos[pid] = {"src": None, "checked": today.isoformat(), "reason": "no freely licensed image"}
    return found


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--fixtures", help="read feeds from DIR/<feed-id>.xml instead of the network")
    ap.add_argument("--no-photos", action="store_true")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    args = ap.parse_args()

    root = Path(args.root)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    today = dt.date.fromisoformat(args.today) if args.today else now.date()
    data = json.loads((root / "data.json").read_text(encoding="utf-8"))
    news = load_json(root / "news.json", {"items": []})
    photos = load_json(root / "photos.json", {})
    people_idx = build_people_index(data)

    known_ids = {i["id"] for i in news["items"]}
    known_titles = {norm_title(i["title"]) for i in news["items"]}
    oldest = today - dt.timedelta(days=CONFIG["maxAgeDays"])
    new_items, flags, feed_status = [], [], {}

    for feed in CONFIG["feeds"]:
        try:
            if args.fixtures:
                p = Path(args.fixtures) / f"{feed['id']}.xml"
                if not p.exists():
                    continue
                raw = p.read_bytes()
            else:
                raw = http_get(feed_url(feed))
            entries = parse_feed(raw, feed)
            feed_status[feed["id"]] = {"ok": True, "entries": len(entries)}
        except Exception as e:  # noqa: BLE001 - one broken feed must not stop the run
            log(f"feed {feed['id']} failed: {e}")
            feed_status[feed["id"]] = {"ok": False, "error": str(e)[:200]}
            continue

        for e in entries:
            if not e["title"] or not e["url"]:
                continue
            text = (e["title"] + " " + e["summary"]).lower()
            if not any(has_term(text, w) for w in CONFIG["mustMatchAny"]):
                continue
            d = e["date"] or today
            if d < oldest or d > today + dt.timedelta(days=1):
                continue
            nt = norm_title(e["title"])
            iid = hashlib.sha1(nt.encode()).hexdigest()[:12]
            if iid in known_ids or nt in known_titles:
                continue
            topics, people = tag_item(text, people_idx)
            if not topics and not people:
                continue
            item = {"id": iid, "title": e["title"], "url": e["url"],
                    "publisher": e["publisher"] or publisher_from_url(e["url"]),
                    "date": d.isoformat(), "topics": topics, "people": people, "feed": feed["id"],
                    "found": now.isoformat()}
            known_ids.add(iid)
            known_titles.add(nt)
            new_items.append(item)
            flags.extend(flags_for(item, text, data))

    items = sorted(new_items + news["items"], key=lambda i: (i["date"], i.get("found", "")), reverse=True)
    items = [i for i in items if i["date"] >= oldest.isoformat()][: CONFIG["maxItems"]]
    news = {"checked": now.isoformat(), "feeds": feed_status, "items": items}
    write_json(root / "news.json", news)

    found_photos = 0
    if not args.no_photos and not args.fixtures:
        found_photos = refresh_photos(data, photos, today)
        write_json(root / "photos.json", photos)

    # one flag per (kind, person/value, url)
    seen, uniq = set(), []
    for f in flags:
        k = (f["kind"], f.get("person") or f.get("pollster") or str(f.get("value", "")), f["article"]["url"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    write_json(root / "review.json", {"generated": now.isoformat(), "flags": uniq})
    (root / "review.md").write_text(render_review(uniq, now), encoding="utf-8")

    ok_feeds = sum(1 for s in feed_status.values() if s["ok"])
    log(f"feeds ok {ok_feeds}/{len(feed_status)} · new items {len(new_items)} · flags {len(uniq)} · photos {found_photos}")
    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write(f"new_items={len(new_items)}\nflags={len(uniq)}\n")
    return 0


def render_review(flags: list[dict], now: dt.datetime) -> str:
    if not flags:
        return f"No items need review ({now.date()}).\n"
    titles = {"status": "Possible status changes", "voterCount": "Possible new voter counts",
              "survey": "Possible new surveys", "registration": "Registration window",
              "impeachment": "Impeachment trial"}
    out = [f"The daily scrape on {now.date()} found {len(flags)} article(s) that may mean `data.json` is out of date.",
           "Nothing on the site's curated facts has changed. Check each article, edit `data.json` by hand (add the article to `sources`), then tick the box.", ""]
    for kind, title in titles.items():
        group = [f for f in flags if f["kind"] == kind]
        if not group:
            continue
        out.append(f"### {title}")
        for f in group:
            a = f["article"]
            who = f" **{f['name']}** (now: {'; '.join(f['current'])}) —" if kind == "status" else ""
            out.append(f"- [ ]{who} [{a['title']}]({a['url']}) · {a['publisher']} · {a['date']}  \n  {f['ask']}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
