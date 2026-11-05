"""Prove Sal's sandbox from inside it (run by sal-check.service, which gets the same sandbox as Sal).

Every address of this machine (argv[1], written at install time) on the other home services,
SSH and other local ports must be blocked: the kernel's filter drops the packets (connect times out
or fails with EPERM) and IPv6 sockets can't be opened; "open" or "refused" would mean the packet got
through. The control: the local DNS stub, the one local address Sal may use, must connect, so the
timeouts are the filter's doing. Home files, user sockets and devices must be out of sight,
the code read-only, and the public internet reachable. Exits 1 if anything is open.
Run outside the sandbox, the same script shows those ports reachable.
"""
import errno
import glob
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

# the machine's other private services, ssh, Sal itself, a node dev server
PORTS = [1984, 8554, 8555, 8090, 8091, 22, 8080, 3000]
BLOCKED = {errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT}
bad = 0


def report(ok, what):
    global bad
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {what}", flush=True)


def connect(host, port, timeout=3):
    """'blocked' (dropped or refused by the sandbox), 'open', 'refused' (reached a closed port), or an error name."""
    try:
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
        return "open"
    except socket.timeout:
        return "blocked"
    except OSError as e:
        if e.errno in BLOCKED:
            return "blocked"
        return "refused" if e.errno == errno.ECONNREFUSED else (errno.errorcode.get(e.errno) or str(e))


report(connect("127.0.0.53", 53) == "open", "control: the local DNS stub (allowed) connects, so the timeouts below are the filter")
hosts = {"127.0.0.1", "::1"}
if len(sys.argv) > 1:
    hosts |= {h.split("%")[0] for h in open(sys.argv[1]).read().split()}
probes = [(h, p) for h in sorted(hosts) for p in PORTS]
with ThreadPoolExecutor(32) as ex:
    results = dict(zip(probes, ex.map(lambda hp: connect(*hp), probes)))
for h in sorted(hosts):
    leaks = {p: results[h, p] for p in PORTS if results[h, p] != "blocked"}
    report(not leaks, f"{h}: ports {', '.join(map(str, PORTS))} " + ("blocked" if not leaks else f"NOT blocked {leaks}"))

for path in ["/home/rgv250cc/.ssh/known_hosts", "/home/rgv250cc/.ssh/id_ed25519", "/run/user/1000/bus"]:
    try:
        open(path, "rb").close()
        report(False, f"{path} is readable")
    except OSError as e:
        report(True, f"{path} out of reach ({errno.errorcode.get(e.errno)})")
try:
    report(not os.listdir("/home"), "/home looks empty")
except OSError:
    report(True, "/home is inaccessible")
report(not glob.glob("/dev/video*"), "no video devices in /dev")
others = [p for p in os.listdir("/proc") if p.isdigit() and os.stat(f"/proc/{p}").st_uid != os.getuid()]
report(not others, f"other users' processes hidden ({len(others)} visible)")
for path, want in [("/srv/sal/code/.probe", False), ("/srv/sal/data/.probe", True)]:
    try:
        open(path, "w").close()
        os.unlink(path)
        report(want, f"{os.path.dirname(path)} is writable")
    except OSError:
        report(not want, f"{os.path.dirname(path)} is read-only")

try:
    addr = socket.getaddrinfo("nominatim.openstreetmap.org", 443, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    report(connect(addr, 443) == "open", f"internet: nominatim.openstreetmap.org ({addr}) port 443 reachable")
except OSError as e:
    report(False, f"internet: DNS failed ({e})")

print("sandbox holds" if not bad else f"{bad} check(s) failed", flush=True)
sys.exit(1 if bad else 0)
