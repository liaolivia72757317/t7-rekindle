"""Run the isolated native integration host; realClient remains false."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


def run(root: Path) -> dict:
    root = root.resolve()
    # The host is a separate process so this driver never initializes the
    # embedded CPython owner that it is testing.
    import build

    host = root / "artifacts/native/bin/x64/Release/T7.RuntimeTests.exe"
    if not host.is_file():
        raise FileNotFoundError(
            "native integration host is missing; run `python scripts/build.py --project native-tests` first"
        )

    original_build_root = build.ROOT
    build.ROOT = root
    fixture = None
    try:
        python_home = build.check_python()
        fixture = build.prepare_runtime_fixture(python_home)
        environment = os.environ.copy()
        environment["PATH"] = str(python_home) + os.pathsep + environment.get("PATH", "")
        environment["LOCALAPPDATA"] = str(fixture / "state")
        completed = subprocess.run(
            [str(host), str(fixture)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                "native integration host failed with exit code "
                f"{completed.returncode}: {details}"
            )
    finally:
        if fixture is not None:
            build.remove_tree(fixture)
        build.ROOT = original_build_root

    # Keep the pure business transition check in this driver.  It loads only
    # the public Business package; the native host above owns the embedded
    # interpreter and has already run in a different process.
    business = root / "src/Business"
    import sys
    sys.path.insert(0, str(business / "runtime"))
    import host_runtime

    cache = root / "artifacts/tmp/integration-cache"
    cache.mkdir(parents=True, exist_ok=True)
    runtime = host_runtime.Runtime(business / "scripts", cache)
    try:
        context = host_runtime.encode({
            "nowMs": 0,
            "advertisedAddress": "127.0.0.1",
            "instancePort": 1,
            "connections": [],
        })
        state = runtime.create(context)
        connected = host_runtime.encode({
            "type": "connected", "eventId": 1, "role": "login", "name": "",
            "connection": 1, "sequence": 0, "serverTimeMs": 0, "command": 0, "body": b"",
        })
        state, sends, timers, logs = runtime.dispatch(connected, state, context)
        if sends or timers or logs:
            raise AssertionError("connected event produced an unexpected transition")
        context = host_runtime.encode({
            "nowMs": 1,
            "advertisedAddress": "127.0.0.1",
            "instancePort": 1,
            "connections": [1],
        })
        authenticated = host_runtime.encode({
            "type": "authenticated", "eventId": 2, "role": "login", "name": "",
            "connection": 1, "sequence": 0, "serverTimeMs": 1, "command": 0, "body": b"",
        })
        state, sends, timers, logs = runtime.dispatch(authenticated, state, context)
        if len(timers) != 1 or timers[0][0] != "1:version":
            raise AssertionError("authenticated event did not schedule the deterministic version timer")
    finally:
        runtime.close()

    return {
        "result": "passed",
        "realClient": False,
        "host": host.relative_to(root).as_posix(),
        "checks": [
            "bridge-owned CPython initializes in a separate host process",
            "three dynamic loopback listeners reach readiness",
            "fragmented AUTH and method3 key reply on all channels",
            "fake client adapter exercises Session start/stop assembly",
            "business connected/authenticated transition and timer contract",
            "wire capture default/off and explicit diagnostic opt-in",
            "journal rotation, cursor bounds, cancellation and cleanup",
            "no client process started",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run(args.root.resolve())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
