#!/usr/bin/env python3
"""Checks data.json before it is published. Exit 1 on errors.

Errors: bad JSON, unknown status, a source id that does not exist, an aspirant
id missing from `people`, a survey without fieldwork/n/moe.
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
for k, (t, u) in src.items():
    if not u.startswith("https://"):
        errors.append(f"sources.{k}: url must be https")

for w in warnings:
    print("warning:", w)
for e in errors:
    print("ERROR:", e)
print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
