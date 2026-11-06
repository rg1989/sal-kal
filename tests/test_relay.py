"""Super-Pharm through home (sal-relay): which chains app.download asks for, and deploy/relay-push.sh sending files
through the same forced command the server uses (rrsync -wo -no-del), with a stand-in ssh that runs it locally.
Run: .venv/bin/python tests/test_relay.py   (needs rsync and rrsync)"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from app.download import chains  # noqa: E402

running = {"SHUFERSAL", "SUPER_PHARM", "RAMI_LEVY"}
assert chains(None, running) is None and chains("", running) is None and chains(" , ", running) is None
assert chains("SUPER_PHARM", running) == ["SUPER_PHARM"]
assert chains(" super_pharm , RAMI_LEVY", running) == ["SUPER_PHARM", "RAMI_LEVY"]
assert chains("VICTORY_NEW_SOURCE", running) == []  # switched off this hour: the run is skipped, not failed

rrsync = shutil.which("rrsync")
if not (rrsync and shutil.which("rsync")):
    print("ok (push not tested: no rsync/rrsync)")
    sys.exit(0)

tmp = tempfile.mkdtemp()
relay, dest = os.path.join(tmp, "relay"), os.path.join(tmp, "server", "SuperPharm")
src = os.path.join(relay, "dumps", "SuperPharm")
os.makedirs(src)
os.makedirs(dest)
for name in ("key", "known_hosts"):
    open(os.path.join(relay, name), "w").close()
with open(os.path.join(relay, "target"), "w") as f:
    f.write("salrelay@server.invalid\n")
fake = os.path.join(tmp, "ssh")
with open(fake, "w") as f:  # what sshd does with the forced command: the client's command goes in SSH_ORIGINAL_COMMAND
    f.write(f"""#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
while a and a[0].startswith("-"):
    a = a[2:] if a[0] in ("-i", "-o", "-l", "-p") else a[1:]
os.environ["SSH_ORIGINAL_COMMAND"] = " ".join(a[1:])
os.execv({rrsync!r}, [{rrsync!r}, "-wo", "-no-del", {dest!r}])
""")
os.chmod(fake, 0o755)
env = dict(os.environ, RELAY_DIR=relay, RELAY_FOLDER="SuperPharm", RELAY_SSH=fake)
push = ["sh", os.path.join(ROOT, "deploy", "relay-push.sh")]

out = subprocess.run(push, env=env, capture_output=True, text=True)
assert out.returncode == 0 and "no new SuperPharm files" in out.stdout, out

files = ["PriceFull7290172900007-001-202611061700.xml", "Promo7290172900007-001-202611061700.xml"]
for n in files:
    with open(os.path.join(src, n), "w") as f:
        f.write("<root/>")
    os.chmod(os.path.join(src, n), 0o600)
    os.utime(os.path.join(src, n), (time.time() - 3600, time.time() - 3600))  # downloaded an hour before the push
with open(os.path.join(src, ".PriceFull7290172900007-002-x.xml.Ab12"), "w") as f:
    f.write("partial")
sent_at = time.time() - 2
out = subprocess.run(push, env=env, capture_output=True, text=True)
assert out.returncode == 0, out
assert "sent 2 SuperPharm files" in out.stdout, out.stdout
assert sorted(os.listdir(dest)) == files, os.listdir(dest)
assert all(stat.S_IMODE(os.stat(os.path.join(dest, n)).st_mode) == 0o640 for n in files)  # readable by group sal
# they land with the time they arrive: the server's index run deletes files older than its own start
assert all(os.stat(os.path.join(dest, n)).st_mtime >= sent_at for n in files)
assert os.listdir(src) == [".PriceFull7290172900007-002-x.xml.Ab12"]  # sent ones dropped here; a temp file never sent

# a file that comes again (same name, new content) replaces the server's copy
with open(os.path.join(src, files[0]), "w") as f:
    f.write("<root>2</root>")
assert subprocess.run(push, env=env, capture_output=True, text=True).returncode == 0
assert open(os.path.join(dest, files[0])).read() == "<root>2</root>"

# the server's account can't read back or delete (the forced command)
pull = subprocess.run(["rsync", "-rt", "-e", fake, "salrelay@server.invalid:", os.path.join(tmp, "pulled")],
                      capture_output=True, text=True)
assert pull.returncode != 0 and not os.path.exists(os.path.join(tmp, "pulled", files[1])), pull
wipe = subprocess.run(["rsync", "-rt", "--delete", "-e", fake, os.path.join(tmp, "empty") + "/",
                       "salrelay@server.invalid:"], capture_output=True, text=True)
assert sorted(os.listdir(dest)) == files, wipe

print("ok")
