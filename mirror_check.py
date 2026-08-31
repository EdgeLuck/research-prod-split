"""Does the server run what you think it runs?

WHY THIS EXISTS

A deploy directory and a working directory are hand-maintained mirrors, and
they drift APART SILENTLY. Nothing alerts. Two real findings from one account:

  * the deployed engine was an older revision, missing two fixes that had been
    written, tested, and never actually shipped;
  * four cron wrappers and one script existed ONLY on the server -- there was
    no local copy at all, so they were absent from every backup and every
    review, and nobody knew until this check listed them.

An earlier version of this check compared four files chosen from memory. That
is the failure mode it was supposed to catch, reproduced inside the tool
itself. This one enumerates everything on both sides.

LINE ENDINGS ARE NOT A DIFFERENCE. A Windows workstation and a Linux server
disagree about CRLF, and without normalization every single file reports as
changed -- which trains you to ignore the output.

READ-ONLY BY DESIGN. This copies nothing and fixes nothing. Deciding which
side is correct is a human's job: sometimes the server is ahead because of an
emergency patch, and auto-syncing would destroy the fix.

    export MIRROR_HOST=user@host
    export MIRROR_REMOTE_DIR=/opt/bot
    python mirror_check.py            # exit 1 on any divergence
    python mirror_check.py --quiet    # problems only
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HOST = os.environ.get("MIRROR_HOST", "")
PORT = os.environ.get("MIRROR_PORT", "22")
SSH_KEY = os.environ.get("MIRROR_SSH_KEY", "")
REMOTE = os.environ.get("MIRROR_REMOTE_DIR", "/opt/bot")

HERE = Path(__file__).resolve().parent
LOCAL_DIRS = [Path(p) for p in
              os.environ.get("MIRROR_LOCAL_DIRS", str(HERE)).split(os.pathsep)]

PATTERNS = ("*.py", "*.sh")


def normalized_md5(data: bytes) -> str:
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def remote_files():
    """{filename: md5} for every script on the server."""
    if not HOST:
        print("set MIRROR_HOST (user@host) first", file=sys.stderr)
        sys.exit(2)

    globs = " ".join(PATTERNS)
    cmd = (f'cd {REMOTE} && for f in {globs}; do [ -f "$f" ] || continue; '
           f'echo "$f $(tr -d \'\\r\' < "$f" | md5sum | cut -d" " -f1)"; done')

    ssh = ["ssh"]
    if SSH_KEY:
        ssh += ["-i", SSH_KEY]
    ssh += ["-p", PORT, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            HOST, cmd]

    r = subprocess.run(ssh, capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        print("could not reach the server:", r.stderr.strip()[:300],
              file=sys.stderr)
        sys.exit(2)

    out = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def local_files():
    """{filename: (md5, path)} across every configured local directory."""
    out = {}
    for directory in LOCAL_DIRS:
        if not directory.is_dir():
            continue
        for pattern in PATTERNS:
            for path in directory.glob(pattern):
                if path.name in out:
                    continue           # first directory listed wins
                out[path.name] = (normalized_md5(path.read_bytes()), path)
    return out


def main():
    ap = argparse.ArgumentParser(description="Compare deployed scripts with local copies.")
    ap.add_argument("--quiet", action="store_true", help="problems only")
    args = ap.parse_args()

    remote = remote_files()
    local = local_files()

    same, differs = [], []
    only_remote = sorted(set(remote) - set(local))
    only_local = sorted(set(local) - set(remote))

    for name in sorted(set(remote) & set(local)):
        (same if remote[name] == local[name][0] else differs).append(name)

    if not args.quiet and same:
        print(f"identical: {len(same)} file(s)")

    for name in differs:
        print(f"DIFFERS      {name}  (local: {local[name][1]})")
    for name in only_remote:
        # The dangerous direction: it exists in production and nowhere else.
        print(f"SERVER ONLY  {name}  -- not in any local directory, "
              f"so it is in no backup and no review")
    for name in only_local:
        print(f"LOCAL ONLY   {name}  -- written but never deployed")

    problems = len(differs) + len(only_remote) + len(only_local)
    if problems:
        print(f"\n{problems} divergence(s). Nothing was changed -- decide which "
              f"side is correct yourself.")
        return 1

    print("mirrors match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
