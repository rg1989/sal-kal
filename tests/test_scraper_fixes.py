"""The downloader fixes (app/scraper_fixes.py): Shufersal's files are skipped once fetched, no other chain's listing
hash changes, status files stay lean, whole and readable, the log loses the per-file listing.
Needs the downloader installed: /srv/sal/downloader/venv/bin/python tests/test_scraper_fixes.py"""
import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
tmp = tempfile.mkdtemp()
os.chdir(tmp)  # importing the downloader opens logging.log in the working folder

from app import scraper_fixes as fx  # noqa: E402

SIG_A = "?sv=2014-02-14&sr=b&sig=AAAA%2Bz&se=2026-11-06T10%3A56%3A42Z&sp=r"
SIG_B = "?sv=2014-02-14&sr=b&sig=BBBB%2Fq&se=2026-11-06T13%3A57%3A01Z&sp=r"
SHUF = "https://pricesprodpublic.blob.core.windows.net/promo/Promo7290027600007-001-313-20261106-030000.gz"
HAZI = "https://hazihinamprod01.blob.core.windows.net/regulatories/Price7290700100008-000-203-20261106-121212.gz"
BINA = "https://supersapir.binaprojects.com/Download.aspx?FileNm=PriceFull7290058156016-001-202611060200.xml"

# links: only an Azure blob link loses its query
assert fx.stable_url(SHUF + SIG_A) == SHUF
assert fx.stable_url(HAZI) == HAZI
assert fx.stable_url(BINA) == BINA
assert fx.stable_url("https://evil.example/x.blob.core.windows.net?sig=1") == "https://evil.example/x.blob.core.windows.net?sig=1"
assert fx.stable_url(None) is None and fx.stable_url("") == ""


def upstream_hash(name, url, size):  # file_entry.py:27-36 at 1.0.19
    return hashlib.sha256(f"{name}\n{url}\n{'' if size is None else size}".encode()).hexdigest()


assert fx.apply() == ["listing hash", "status writes"]
assert fx.apply() == ["listing hash", "status writes"]  # twice is once

from il_supermarket_scarper.utils.files.file_entry import FileEntry  # noqa: E402
from il_supermarket_scarper.utils.databases.json_file import JsonDataBase  # noqa: E402
from il_supermarket_scarper.utils.files.verified_downloads import VerifiedDownloads  # noqa: E402

name = "Promo7290027600007-001-313-20261106-030000"
a, b = FileEntry(name=name, url=SHUF + SIG_A, size=None), FileEntry(name=name, url=SHUF + SIG_B, size=None)
assert a.listing_hash() == b.listing_hash() == upstream_hash(name, SHUF, None)
assert FileEntry(name=name, url=SHUF + SIG_A, size=10).listing_hash() != a.listing_hash()  # size still counts
assert FileEntry(name=name + "x", url=SHUF + SIG_A, size=None).listing_hash() != a.listing_hash()  # so does the name
# every other chain hashes exactly as before: nothing it has fetched is fetched again
for n, u, s in ((name, HAZI, 5945), ("PriceFull7290058156016-001-202611060200", BINA, None), ("x", None, None)):
    assert FileEntry(name=n, url=u, size=s).listing_hash() == upstream_hash(n, u, s), u


def child_hash(_):
    return FileEntry(name=name, url=SHUF + SIG_B, size=None).listing_hash()


# the runner forks its workers after apply(): they hash the same way
with multiprocessing.get_context("fork").Pool(1) as pool:
    assert pool.map(child_hash, [0]) == [a.listing_hash()]

# status: the write-only rows aren't written, the file is compact, whole, and nothing is left beside it
status = os.path.join(tmp, "status")
db = JsonDataBase("Shufersal", base_path=status)
path = os.path.join(status, "shufersal.json")
db.insert_document("events", {"status": "saw"})
db.insert_documents("events", [{"status": "collected"}])
db.insert_document("global_status", {"status": "started", "folder_content": ["…"] * 1000})
assert json.load(open(path)) == {}, open(path).read()
verified = VerifiedDownloads(db)
verified.record(name + ".gz", "d1", "2026-11-06T03:00:00", "created", listing_hash=a.listing_hash())
text = open(path).read()
data = json.loads(text)
assert list(data) == ["verified_downloads"] and len(data["verified_downloads"]) == 1, data
assert "\n" not in text and ": " not in text  # compact
assert sorted(os.listdir(status)) == ["shufersal.json"]

# a new run reads it back and skips the same file under a new signature, but not a new file


async def listed(*entries):
    for e in entries:
        yield e


async def kept(v, *entries):
    return [e.name async for e in v.filter_already_downloaded(listed(*entries))]

fresh = FileEntry(name="Promo7290027600007-001-313-20261106-060000", url=SHUF.replace("030000", "060000") + SIG_B,
                  size=None)
assert asyncio.run(kept(VerifiedDownloads(JsonDataBase("Shufersal", base_path=status)), b, fresh)) == [fresh.name]

# a failed write leaves the old file whole and no temp file
real_dump = json.dump


def broken(*args, **kw):
    raise OSError("disk full")


json.dump = broken
try:
    try:
        verified.record("other.gz", "d2", None, "created", listing_hash="h2")
        raise AssertionError("expected the write to fail")
    except OSError:
        pass
finally:
    json.dump = real_dump
assert open(path).read() == text and sorted(os.listdir(status)) == ["shufersal.json"]

# the log: INFO and up, without the per-file listing
fx.quiet()
fx.quiet()
from il_supermarket_scarper.utils.core.logger import Logger  # noqa: E402
seen = []


class Grab(logging.Handler):
    def emit(self, record):
        seen.append(record.getMessage())


Logger.logger.addHandler(Grab())
Logger.info("- file /srv/sal/data/dumps/HaziHinam/x.xml: size 5945")
Logger.debug("noise")
Logger.info("Folder /srv/sal/data/dumps/HaziHinam: Num of Files= 1")
Logger.logger.warning("a warning")
assert seen == ["Folder /srv/sal/data/dumps/HaziHinam: Num of Files= 1", "a warning"], seen
assert len(Logger.logger.filters) == 1

print("ok")
