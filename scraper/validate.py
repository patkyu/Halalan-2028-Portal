#!/usr/bin/env python3
"""Checks data.json before it is published. Exit 1 on errors.

Errors: bad JSON, unknown status, a source id that does not exist, an aspirant
id missing from `people`, a survey without fieldwork/n/moe, a job description
missing a field, an official ballot without a source or with bad numbers, a poll alias
for an unknown person.
Warnings: an entry with no source at all (every figure should have one).
"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
d = json.loads((root / "data.json").read_text(encoding="utf-8"))
errors, warnings = [], []
src = d["sources"]


def check_src(where, ids):
    if not ids:
        warnings.append(f"{where}: no source")
    for s in ids or []:
        if s not in src:
            errors.append(f"{where}: unknown source id '{s}'")


for i, v in enumerate(d["voterCounts"]):
    if v["type"] not in ("count", "projection"):
        errors.append(f"voterCounts[{i}]: type must be count or projection")
    check_src(f"voterCounts[{i}] {v['label']}", v.get("src"))
for pos, lst in d["aspirants"].items():
    for a in lst:
        if a["id"] not in d["people"]:
            errors.append(f"aspirants.{pos}: '{a['id']}' not in people")
        if a["status"] not in ("ok", "warn", "vio", "no"):
            errors.append(f"aspirants.{pos}.{a['id']}: bad status {a['status']}")
        check_src(f"aspirants.{pos}.{a['id']}", a.get("src"))
for pos, lst in d["surveys"].items():
    for i, s in enumerate(lst):
        for k in ("pollster", "fieldwork", "n", "moe", "rows"):
            if k not in s:
                errors.append(f"surveys.{pos}[{i}]: missing {k}")
        check_src(f"surveys.{pos}[{i}] {s.get('pollster')}", s.get("src"))
for e in d["timeline"]:
    for s in e.get("src", []):
        if s not in src:
            errors.append(f"timeline {e['label']}: unknown source id '{s}'")
JOB_KEYS = ("id", "pos", "tl", "level", "seats", "term", "pick", "duties", "not", "quals", "ask", "src")
job_ids = set()
for j in d.get("jobs", []):
    for k in JOB_KEYS:
        if k not in j:
            errors.append(f"jobs.{j.get('id')}: missing {k}")
    if j.get("level") not in ("national", "local"):
        errors.append(f"jobs.{j.get('id')}: level must be national or local")
    job_ids.add(j.get("id"))
    check_src(f"jobs.{j.get('id')}", j.get("src"))

b = d.get("ballot", {})
if b.get("official"):
    if not b.get("released"):
        errors.append("ballot: official list needs a released date")
    if not b.get("src"):
        errors.append("ballot: official list needs a source (COMELEC)")
    if not b.get("positions"):
        errors.append("ballot: official list has no positions")
for s_ in b.get("src", []):
    if s_ not in src:
        errors.append(f"ballot: unknown source id '{s_}'")
for p in b.get("positions", []):
    if p.get("job") not in job_ids:
        errors.append(f"ballot: position '{p.get('job')}' has no job description")
    if not isinstance(p.get("max"), int) or p["max"] < 1:
        errors.append(f"ballot.{p.get('job')}: max must be a positive integer")
    nos = [c.get("no") for c in p.get("candidates", [])]
    if any(not isinstance(n, int) for n in nos) or len(set(nos)) != len(nos):
        errors.append(f"ballot.{p.get('job')}: candidate numbers must be unique integers")
    if any(not c.get("name") for c in p.get("candidates", [])):
        errors.append(f"ballot.{p.get('job')}: every candidate needs a name")
poll = d.get("poll", {})
known = set(d["people"]) | {e.get("id") for e in poll.get("extra", [])}
for pid in poll.get("aliases", {}):
    if pid not in known:
        errors.append(f"poll.aliases: '{pid}' is not in people or poll.extra")
for e in poll.get("extra", []):
    if not e.get("id") or not e.get("name"):
        errors.append("poll.extra: every entry needs an id and a name")
    if e.get("id") in d["people"] or e.get("id") == "other":
        errors.append(f"poll.extra: id '{e.get('id')}' clashes with an existing id")
if poll.get("open") and not poll.get("positions"):
    errors.append("poll: open but has no positions")

for k, (t, u) in src.items():
    if not u.startswith("https://"):
        errors.append(f"sources.{k}: url must be https")

for w in warnings:
    print("warning:", w)
for e in errors:
    print("ERROR:", e)
print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
