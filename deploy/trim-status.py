"""Run before every download (sal-download ExecStartPre): keeps the downloader's status files small.

The downloader (israeli-supermarket-scarpers) keeps one JSON file per chain in dumps/status and, for every file
it saves, reads the whole JSON and writes it back twice (pretty-printed and sorted). Nothing prunes these files, so
each run got slower than the last. By 2026-11-06 they held 189 MB after 28 hours, and every run hit the 3-hour limit
with 4 cores busy.
Of what it keeps, only `verified_downloads` is ever read back (once per run: files already fetched are skipped).
`events` and `global_status` are only written. So: drop those two, keep `verified_downloads` for KEEP_DAYS (price files
are deleted after 2 days, so older rows can't be needed), and rotate the downloader's own debug log.
Each file is replaced atomically. A file cut off by a run killed mid-write (the downloader would reset it to empty and
fetch that chain from scratch) keeps every complete verified row found in it.
Since 2026-11-06 the downloader runs with app/scraper_fixes.py (no events/global_status, atomic compact writes), so
this is the safety net; it also removes the temp files a killed run can leave (<chain>.json.<pid>-<thread>.tmp)."""
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

DATA = sys.argv[1] if len(sys.argv) > 1 else "/srv/sal/data"
KEEP_DAYS = 3
LOG_MAX = 50 * 1024 * 1024
cut = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)


def recent(doc):
    try:
        ts = datetime.fromisoformat(str(doc.get("system_timestamp")))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone(timedelta(hours=2)))
    return ts >= cut


def salvage(text):
    """The complete verified rows (flat objects with a save_decision) in a cut-off file."""
    dec, rows = json.JSONDecoder(), []
    for m in re.finditer(r'\{\s*"(?:content_sha256|entry_id|file_name)"', text):
        try:
            doc, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(doc, dict) and "save_decision" in doc and "listing_hash" in doc:
            rows.append(doc)
    return {"verified_downloads": rows}


for tmp in glob.glob(os.path.join(DATA, "dumps", "status", "*.json.*.tmp")):
    try:
        os.unlink(tmp)
    except OSError:
        pass

before = after = 0
for path in glob.glob(os.path.join(DATA, "dumps", "status", "*.json")):
    size = os.path.getsize(path)
    before += size
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        after += size
        continue
    try:
        data = json.loads(text)
    except ValueError:
        data = salvage(text)
        print(f"{os.path.basename(path)}: cut off, kept {len(data['verified_downloads'])} verified rows")
    if not isinstance(data, dict):
        after += size
        continue
    kept = {"verified_downloads": [d for d in data.get("verified_downloads") or [] if isinstance(d, dict) and recent(d)]}
    if "_metadata" in data:
        kept["_metadata"] = data["_metadata"]
    tmp = path + ".trim"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(kept, f, default=str)
    os.replace(tmp, path)
    after += os.path.getsize(path)

log = os.path.join(DATA, "logging.log")
if os.path.exists(log) and os.path.getsize(log) > LOG_MAX:
    os.replace(log, log + ".1")
print(f"status files {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")
