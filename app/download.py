"""The server's price-file download (sal-download.service): python -m app.download, run from /srv/sal/code.

The downloader (il-supermarket-scraper from PyPI, deploy/downloader-requirements.txt) with Sal's two fixes
(app/scraper_fixes.py). Replaces the upstream clone's main.py, which the PyPI package doesn't ship. Same run as before:
every chain, NUMBER_OF_PROCESSES workers, one pass, files under STORAGE_PATH, status JSON in STATUS_DATABASE_PATH.
SAL_CHAINS (e.g. SUPER_PHARM) limits it to those chains: sal-relay.service fetches Super-Pharm at home this way.
Only the standard library and the downloader: it runs in the downloader's own venv, not the app's.
"""
import os

from app import scraper_fixes


def chains(wanted, running):
    """The chains asked for (SAL_CHAINS, comma-separated) that the downloader runs right now; None = every chain.
    Asking it for one it has switched off for the hour (its stability rules) would fail the run."""
    asked = [c.strip().upper() for c in (wanted or "").split(",") if c.strip()]
    return [c for c in asked if c in running] if asked else None


def main():
    data = os.environ.get("SAL_DATA", "/srv/sal/data")
    storage = os.environ.get("STORAGE_PATH", os.path.join(data, "dumps"))
    status = os.environ.get("STATUS_DATABASE_PATH", os.path.join(storage, "status"))
    os.makedirs(data, exist_ok=True)
    os.chdir(data)  # the downloader opens logging.log (and keeps cookies) in the working folder, at import
    print("scraper_fixes: applied", ", ".join(scraper_fixes.apply()) or "nothing", flush=True)
    scraper_fixes.quiet()
    from il_supermarket_scarper import ScarpingTask, ScraperFactory
    only = chains(os.environ.get("SAL_CHAINS"), set(ScraperFactory.all_scrapers_name()))
    if only == []:
        print(f"none of {os.environ.get('SAL_CHAINS')} runs at this hour", flush=True)
        return
    task = ScarpingTask(enabled_scrapers=only, multiprocessing=int(os.environ.get("NUMBER_OF_PROCESSES", "4")),
                        output_configuration={"output_mode": "disk", "base_storage_path": storage},
                        status_configuration={"database_type": "json", "base_path": status})
    task.start(single_pass=True)
    task.join()


if __name__ == "__main__":
    main()
