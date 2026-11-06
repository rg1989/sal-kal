"""Two fixes applied to the price-file downloader (OpenIsraeliSupermarkets/israeli-supermarket-scarpers) before it runs.

The downloader skips a file it already has only when the file's listing hash (name, URL, size) is in its status file.
1. Shufersal's links carry an Azure signature and expiry (?sv=…&sig=…&se=…) that change on every listing, so none of
   its files was ever skipped: about 1,500 downloads again a run, each one another status row (2026-11-06: 10,134 rows
   for 4,913 files; its time per run went 28, 38, 43, 48, 52 min as the file grew).
2. Every status row reads the chain's whole JSON file and writes it back twice (indent=4), and most rows are `events`
   and `global_status` that nothing reads (deploy/trim-status.py deletes them before every run). A run killed in
   mid-write left a cut-off file, which the downloader resets to empty: it then forgets every file it had.
apply() patches the classes, so the forked worker processes have it too:
- the listing hash leaves out the query string of *.blob.core.windows.net links only (name, path and size still
  identify the file: every Shufersal file name has its own time); every other link hashes as before, so no other
  chain downloads anything again;
- `events`, `global_status` and the last-modified stamp are not written (only `verified_downloads` is ever read);
- the file is written compact, to a temp file then os.replace (never cut off).
quiet() drops the per-file folder listing from the log (67,609 of 88,932 lines in one run) and the DEBUG lines.
Checked against il-supermarket-scraper 1.0.19 (TESTED). On another version a fix whose piece is missing is skipped
with a warning: downloading goes on unpatched rather than stopping.
Their licence asks changes to be noted: README credits the downloader and says Sal-Kal patches these two behaviours.
"""
import dataclasses
import json
import logging
import os
import sys
import threading
from urllib.parse import urlsplit, urlunsplit

TESTED = "1.0.19"
UNREAD = frozenset({"events", "global_status"})
_done = set()


def _say(msg):
    print(f"scraper_fixes: {msg}", file=sys.stderr, flush=True)


def stable_url(url):
    """The link as it identifies the file: an Azure blob link without its rotating signature, anything else as is."""
    if not url or "?" not in url:
        return url
    parts = urlsplit(url)
    if (parts.hostname or "").endswith(".blob.core.windows.net"):
        return urlunsplit(parts._replace(query=""))
    return url


def _fix_listing_hash():
    from il_supermarket_scarper.utils.files.file_entry import FileEntry
    original = FileEntry.listing_hash

    def listing_hash(self):
        url = stable_url(self.url)
        return original(self if url == self.url else dataclasses.replace(self, url=url))

    FileEntry.listing_hash = listing_hash


def _fix_status_writes():
    from il_supermarket_scarper.utils.databases.json_file import JsonDataBase
    one, many = JsonDataBase._do_insert_document, JsonDataBase._do_insert_documents
    for name in ("_get_database_file_path", "_write_database", "_update_last_modified"):
        if not hasattr(JsonDataBase, name):  # fail here, before patching anything, not mid-run
            raise AttributeError(f"JsonDataBase has no {name}")

    def insert_one(self, collection_name, document):
        if collection_name not in UNREAD:
            one(self, collection_name, document)

    def insert_many(self, collection_name, documents):
        if collection_name not in UNREAD:
            many(self, collection_name, documents)

    def write(self, data):
        path = self._get_database_file_path()
        tmp = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(sorted(data.items())), f, default=str, separators=(",", ":"))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    JsonDataBase._do_insert_document = insert_one
    JsonDataBase._do_insert_documents = insert_many
    JsonDataBase._write_database = write
    JsonDataBase._update_last_modified = lambda self: None


def apply():
    """Patch the downloader in this process (call before it starts; forked workers inherit it). Returns what applied."""
    try:
        from importlib.metadata import version
        have = version("il-supermarket-scraper")
    except Exception:  # noqa: BLE001 - only for the message
        have = "?"
    if have != TESTED:
        _say(f"il-supermarket-scraper {have}, fixes checked against {TESTED}")
    for name, fix in (("listing hash", _fix_listing_hash), ("status writes", _fix_status_writes)):
        if name in _done:
            continue
        try:
            fix()
            _done.add(name)
        except (ImportError, AttributeError) as e:
            _say(f"{name} not patched ({type(e).__name__}: {e}); running without it")
    return sorted(_done)


class _NoFileList(logging.Filter):
    def filter(self, record):
        return not str(record.msg).startswith("- file ")


def quiet():
    """INFO and up, without the line per file in the dump folder that ends every chain's run."""
    try:
        from il_supermarket_scarper.utils.core.logger import Logger
        Logger.logger.setLevel(logging.INFO)
        if not any(isinstance(f, _NoFileList) for f in Logger.logger.filters):
            Logger.logger.addFilter(_NoFileList())
    except (ImportError, AttributeError) as e:
        _say(f"log not quietened ({type(e).__name__}: {e})")
