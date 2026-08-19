#!/usr/bin/env python3
"""
One-off maintenance script: find near-duplicate entries in data/products.json
that slipped past the old exact-string dedup in update_data.py (e.g. same
game re-discovered days apart with a slightly different punctuation/spacing
in the Chinese name), and flag suspicious source links that appear to have
been copy-pasted onto the wrong product.

Confirmed duplicates found by manual inspection at time of writing:
  - id 75 vs id 107  ("幻想水滸傳 STAR LEAP" / "幻想水滸傳：星躍")
  - id 64 vs id 78   ("超級機器人大戰ZII" / "超級機器人大戰Z II")
This script generalizes that check across the whole file so future runs
catch the same class of bug, not just these two pairs.

Usage:
    python3 dedupe_products.py                 # dry run, prints report only
    python3 dedupe_products.py --apply         # writes changes back to file
    python3 dedupe_products.py --path data/products.json --apply
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict


def normalize_name(name):
    if not name:
        return ""
    return re.sub(r"[\s:：\-－_·,\.，。]+", "", str(name).lower())


def completeness_score(g):
    """Prefer keeping the entry with more filled-in information when two
    records represent the same game."""
    score = 0
    if g.get("launchEst") and g["launchEst"] not in ("", "待定", "TBD", "N/A"):
        score += 1
    if g.get("sourceLinks"):
        score += len(g["sourceLinks"])
    if g.get("threatAnalysis"):
        score += 1
    if g.get("developer") and g["developer"] not in ("未知", "Unknown", ""):
        score += 1
    # later "verified"/"updatedAt" dates are worth a little more, string
    # compare works fine since both use YYYY-MM-DD-ish formats
    score += 0.1 * len(g.get("verified", "") + g.get("updatedAt", ""))
    return score


def find_duplicate_groups(products):
    groups = defaultdict(list)
    for g in products:
        key = normalize_name(g.get("name", "")) or normalize_name(g.get("nameEn", ""))
        if key:
            groups[key].append(g)
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_reused_links(products):
    """A source link URL that appears on two different-named products is
    almost certainly a copy-paste/hallucination error, not a legitimate
    shared page. Flag for manual review — we don't auto-fix content we
    can't verify."""
    url_to_names = defaultdict(set)
    for g in products:
        for link in g.get("sourceLinks", []):
            url = link.get("url", "")
            if url:
                url_to_names[url].add(g.get("name", "?"))
    return {url: names for url, names in url_to_names.items() if len(names) > 1}


def main():
    apply = "--apply" in sys.argv
    path_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--path" and i + 1 < len(sys.argv):
            path_arg = sys.argv[i + 1]
    path = Path(path_arg) if path_arg else Path("data/products.json")

    if not path.exists():
        print(f"❌ File not found: {path}")
        print("   Run this from your repo root, or pass --path data/products.json")
        sys.exit(1)

    products = json.loads(path.read_text(encoding="utf-8"))
    print(f"Loaded {len(products)} products from {path}\n")

    # --- Duplicate detection ---
    dupe_groups = find_duplicate_groups(products)
    to_remove_ids = set()
    if not dupe_groups:
        print("✅ No near-duplicate names found.")
    else:
        print(f"⚠️  Found {len(dupe_groups)} duplicate group(s):\n")
        for key, members in dupe_groups.items():
            members_sorted = sorted(members, key=completeness_score, reverse=True)
            keep = members_sorted[0]
            drop = members_sorted[1:]
            print(f"  Group: {[m.get('name') for m in members]}")
            print(f"    → keep id={keep.get('id')} ({keep.get('name')})")
            for d in drop:
                print(f"    → drop id={d.get('id')} ({d.get('name')})")
                to_remove_ids.add(d.get("id"))
            print()

    # --- Suspicious reused links ---
    reused = find_reused_links(products)
    if reused:
        print(f"⚠️  Found {len(reused)} source link(s) reused across different products (likely copy-paste errors — NOT auto-fixed, please check manually):\n")
        for url, names in reused.items():
            print(f"  {url}\n    used by: {', '.join(names)}\n")
    else:
        print("✅ No suspicious reused source links found.")

    if not apply:
        print("\nDry run only — nothing written. Re-run with --apply to remove the duplicate entries listed above.")
        return

    if to_remove_ids:
        before = len(products)
        products = [g for g in products if g.get("id") not in to_remove_ids]
        path.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ Removed {before - len(products)} duplicate entrie(s). {len(products)} products remain. Written to {path}.")
    else:
        print("\nNothing to remove.")


if __name__ == "__main__":
    main()
