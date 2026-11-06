"""Write deploy/downloader-requirements.txt: il-supermarket-scraper==VERSION and every dependency, each pinned with the
sha256 of the file pip picks for this machine (Linux x86-64, the downloader's Python 3.12), for pip --require-hashes.
  /srv/sal/downloader/venv/bin/python deploy/lock-downloader.py 1.0.20 [constraints.txt]
Resolves with pip itself (--dry-run --report), downloading nothing but metadata. A constraints file (e.g. pip freeze of
the running venv) keeps the dependencies at those versions. Read app/scraper_fixes.py's TESTED note before upgrading."""
import json
import os
import subprocess
import sys
import tempfile

version = sys.argv[1]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloader-requirements.txt")
with tempfile.TemporaryDirectory() as tmp:
    report = os.path.join(tmp, "report.json")
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--dry-run",
           "--ignore-installed", "--report", report, f"il-supermarket-scraper=={version}"]
    if len(sys.argv) > 2:
        cmd += ["-c", sys.argv[2]]
    subprocess.run(cmd, check=True)
    with open(report, encoding="utf-8") as f:
        picked = json.load(f)["install"]
lines = []
for item in sorted(picked, key=lambda i: i["metadata"]["name"].lower()):
    sha = item["download_info"]["archive_info"]["hashes"]["sha256"]
    lines.append(f"{item['metadata']['name']}=={item['metadata']['version']} --hash=sha256:{sha}")
head = (f"# The server's price-file downloader: il-supermarket-scraper {version} from PyPI and its dependencies, exact\n"
        "# versions with the sha256 of each file (deploy/install.sh downloader: pip --require-hashes). Linux x86-64,\n"
        "# Python 3.12. Made by deploy/lock-downloader.py; don't edit by hand.\n")
with open(OUT, "w", encoding="utf-8") as f:
    f.write(head + "\n".join(lines) + "\n")
print(f"{OUT}: {len(lines)} packages")
