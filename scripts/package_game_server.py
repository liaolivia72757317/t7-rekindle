"""Package the standalone Rust service sources for a target-host build."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "t7-game-server-source"


def package(output: Path) -> Path:
    service = ROOT / "game-server"
    files = {"LICENSE": ROOT / "LICENSE"}
    for name in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "THIRD-PARTY.md"):
        files[name] = service / name
    for directory, extensions in (
        ("src", {".rs"}),
        ("tests", {".rs"}),
        ("dev", {".html", ".js", ".css"}),
        ("deploy", {".md", ".json", ".service", ".timer", ".sh", ".example"}),
    ):
        for source in sorted((service / directory).rglob("*")):
            if source.suffix in extensions:
                files[source.relative_to(service).as_posix()] = source

    entries = {}
    for name, source in files.items():
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"invalid source file: {name}")
        if source.resolve() != source.absolute():
            raise ValueError(f"linked source directory: {name}")
        entries[name] = source.read_bytes()
    entries["README.md"] = entries["deploy/README.md"]
    entries["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(entries.items())
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        try:
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name, data in sorted(entries.items()):
                    info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{name}")
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
        except BaseException:
            output.unlink()
            raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(package(args.output))


if __name__ == "__main__":
    main()
