"""Fail if an Android APK/AAB contains a native library below 16 KB ELF alignment.

APK entries are also checked for 16 KB ZIP alignment when stored uncompressed.
For an AAB, bundletool/Play must still validate the generated APK alignment.
"""

from __future__ import annotations

import argparse
import io
import struct
import zipfile
from pathlib import Path


def load_alignments(blob: bytes) -> list[int]:
    if blob[:4] != b"\x7fELF" or blob[5] not in (1, 2):
        raise ValueError("not a supported ELF file")
    endian = "<" if blob[5] == 1 else ">"
    if blob[4] == 2:  # ELF64
        phoff = struct.unpack_from(endian + "Q", blob, 32)[0]
        entsize, count = struct.unpack_from(endian + "HH", blob, 54)
        align_offset = 48
    else:  # ELF32
        phoff = struct.unpack_from(endian + "I", blob, 28)[0]
        entsize, count = struct.unpack_from(endian + "HH", blob, 42)
        align_offset = 28
    alignments = []
    for index in range(count):
        start = phoff + index * entsize
        if struct.unpack_from(endian + "I", blob, start)[0] == 1:  # PT_LOAD
            alignments.append(struct.unpack_from(endian + ("Q" if blob[4] == 2 else "I"), blob, start + align_offset)[0])
    if not alignments:
        raise ValueError("no ELF load segments")
    return alignments


def check(path: Path) -> list[str]:
    problems: list[str] = []
    inspected = 0
    with zipfile.ZipFile(path) as archive, path.open("rb") as raw:
        entries = [entry for entry in archive.infolist() if entry.filename.endswith(".so")]
        for entry in entries:
            name = entry.filename
            try:
                inspected += 1
                alignments = load_alignments(archive.read(entry))
                if any(align < 16384 for align in alignments):
                    problems.append(f"{name}: ELF load alignment {alignments}")
                if path.suffix == ".apk" and entry.compress_type == zipfile.ZIP_STORED:
                    raw.seek(entry.header_offset + 26)
                    filename_length, extra_length = struct.unpack("<HH", raw.read(4))
                    offset = entry.header_offset + 30 + filename_length + extra_length
                    if offset % 16384:
                        problems.append(f"{name}: ZIP data offset {offset} is not 16 KB aligned")
            except (ValueError, struct.error) as exc:
                problems.append(f"{name}: could not inspect ELF ({exc})")
        # Chaquopy stores pip wheels inside .imy (ZIP) assets. These libraries
        # are extracted at runtime, and the Android linker checks them too.
        for package in archive.infolist():
            if not package.filename.endswith(".imy"):
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(archive.read(package))) as nested:
                    for entry in nested.infolist():
                        if not entry.filename.endswith(".so"):
                            continue
                        inspected += 1
                        name = f"{package.filename}!{entry.filename}"
                        try:
                            alignments = load_alignments(nested.read(entry))
                            if any(align < 16384 for align in alignments):
                                problems.append(f"{name}: ELF load alignment {alignments}")
                        except (ValueError, struct.error) as exc:
                            problems.append(f"{name}: could not inspect ELF ({exc})")
            except zipfile.BadZipFile as exc:
                problems.append(f"{package.filename}: could not inspect Chaquopy archive ({exc})")
        if not inspected:
            problems.append("No native libraries found; expected Chaquopy and Python extensions")
        print(f"Inspected {inspected} native libraries in {path.name}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    problems = check(args.archive)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("All native ELF load segments meet 16 KB alignment")
    return bool(problems)


if __name__ == "__main__":
    raise SystemExit(main())
