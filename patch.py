#!/usr/bin/env python3
"""
FMA2 Undub Patcher
==================

Three ways to create the undubbed ISO:

  1) Full pipeline (both ISOs + auto-builds ffmpeg + burns subtitles):
     python3 patch.py full <usa_iso> <jp_iso> [output_iso]

  2) Audio-only (both ISOs, no subs, no ffmpeg needed):
     python3 patch.py audio <usa_iso> <jp_iso> [output_iso]

  3) Apply xdelta patch (USA ISO + xdelta file):
     python3 patch.py xdelta <usa_iso> <xdelta_file> [output_iso]

Options:
    --generate-xdelta   Also create an xdelta patch file after patching
    --skip-verify       Skip MD5 hash verification
    --dump-mkv <dir>    Export subtitled cutscenes as MKV files to <dir>
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile

from dsi_muxer import DSI
from racjin import compress, decompress

from lib.constants import (
    CFC_TRACK_TABLE_DIR_OFFSET,
    DSI_NAMES,
    EXPECTED_HASHES,
    SCEI_BANK_INDICES,
    SECTOR,
    SUBS_DIR,
    XA_TRACK_COUNT,
    XA_TRACK_RECORDS_OFFSET,
)
from lib.ffmpeg import find_or_build_ffmpeg
from lib.iso import find_file_in_iso, update_dir_entry, verify_iso
from lib.video import build_subtitled_dsi, dump_mkv

# =============================================================================
# Core patching
# =============================================================================


def do_audio(
    usa_iso_path: str,
    jp_iso_path: str,
    out_iso_path: str,
    dsi_files: dict[str, str] | None = None,
) -> None:
    """Core patcher: JP XA.PAK + JP combat banks + JP (or subtitled) cutscenes.

    Preserves the retail file order: CFC.DIG grows in place to hold the
    patched track table and JP combat banks, then XA.PAK, the 12 cutscenes,
    and DATA0 follow sequentially, shifted to later sectors as needed.

    Args:
        dsi_files: Optional {name: path} of pre-built subtitled DSI files
            written in place of the raw JP cutscenes (used by full mode).
    """
    print("Reading JP ISO...")
    with open(jp_iso_path, "rb") as f:
        jp_data = f.read()
    print(f"  USA: {os.path.getsize(usa_iso_path):,} bytes")
    print(f"  JP:  {len(jp_data):,} bytes")

    print("\nCopying USA ISO as base...")
    shutil.copy2(usa_iso_path, out_iso_path)

    with open(out_iso_path, "rb") as f:
        iso_header = f.read(10 * 1024 * 1024)

    usa_cfc_info = find_file_in_iso(iso_header, b"CFC.DIG;1")
    assert usa_cfc_info is not None, "CFC.DIG not found in USA ISO"
    usa_cfc_sector, usa_cfc_size, usa_cfc_entry = usa_cfc_info

    jp_cfc_info = find_file_in_iso(jp_data[: 10 * 1024 * 1024], b"CFC.DIG;1")
    assert jp_cfc_info is not None, "CFC.DIG not found in JP ISO"
    jp_cfc_sector = jp_cfc_info[0]

    # The shifted layout overwrites DATA0's original location before DATA0
    # is re-written at the end, so grab its contents up front
    data0_info = find_file_in_iso(iso_header, b"DATA0")
    data0_content = b""
    if data0_info:
        with open(out_iso_path, "rb") as f:
            f.seek(data0_info[0] * SECTOR)
            data0_content = f.read(data0_info[1])

    # --- Step 1: Patch XA track offset table ---
    print(f"\n{'=' * 60}")
    print("Step 1: XA track offset table")
    print(f"{'=' * 60}")

    for label, path, cfc_sec in [
        ("USA", usa_iso_path, usa_cfc_sector),
        ("JP", jp_iso_path, jp_cfc_sector),
    ]:
        with open(path, "rb") as f:
            f.seek(cfc_sec * SECTOR + CFC_TRACK_TABLE_DIR_OFFSET)
            us, uc, _uf, ud = struct.unpack("<IIII", f.read(16))
            f.seek(cfc_sec * SECTOR + us * SECTOR)
            raw = f.read(uc)
        decompressed = decompress(raw, ud)
        if label == "USA":
            usa_decomp = bytearray(decompressed)
        else:
            jp_decomp = decompressed

    patched = bytearray(usa_decomp)
    changed = 0
    for t in range(XA_TRACK_COUNT):
        eoff = 0x30 + t * 0x10
        if jp_decomp[eoff : eoff + 8] != usa_decomp[eoff : eoff + 8]:
            patched[eoff : eoff + 8] = jp_decomp[eoff : eoff + 8]
            changed += 1

    # The audio data is JP, so its playback records (pitch, gain, channel
    # count) must be JP too — USA records flip mono/stereo on 15 tracks,
    # which hangs the IOP streamer on real hardware
    rec_changed = 0
    for t in range(XA_TRACK_COUNT):
        roff = XA_TRACK_RECORDS_OFFSET + t * 8
        if jp_decomp[roff : roff + 8] != usa_decomp[roff : roff + 8]:
            patched[roff : roff + 8] = jp_decomp[roff : roff + 8]
            rec_changed += 1

    cfc2_comp = compress(bytes(patched))
    cfc2_sectors = (len(cfc2_comp) + SECTOR - 1) // SECTOR
    print(f"  Patched {changed}/{XA_TRACK_COUNT} track offsets")
    print(f"  Patched {rec_changed}/{XA_TRACK_COUNT} playback records")

    # --- Step 2: Grow CFC.DIG in place (track table + combat banks) ---
    print(f"\n{'=' * 60}")
    print("Step 2: Grow CFC.DIG (track table + combat banks)")
    print(f"{'=' * 60}")

    write_sector = usa_cfc_sector + (usa_cfc_size + SECTOR - 1) // SECTOR
    banks_patched = 0

    with open(out_iso_path, "r+b") as f:
        # Patched track table goes right after the retail CFC.DIG data
        f.seek(write_sector * SECTOR)
        f.write(cfc2_comp)
        f.write(b"\x00" * (cfc2_sectors * SECTOR - len(cfc2_comp)))
        f.seek(usa_cfc_sector * SECTOR + CFC_TRACK_TABLE_DIR_OFFSET)
        f.write(struct.pack("<I", write_sector - usa_cfc_sector))
        f.write(struct.pack("<I", len(cfc2_comp)))
        write_sector += cfc2_sectors
        print(f"  Track table: {cfc2_sectors} sectors")

        # JP combat bark SCEI banks, appended inside the grown CFC.DIG
        for idx in SCEI_BANK_INDICES:
            jp_entry_off = jp_cfc_sector * SECTOR + idx * 16
            jp_s, jp_c, jp_f, jp_d = struct.unpack(
                "<IIII", jp_data[jp_entry_off : jp_entry_off + 16]
            )
            if jp_s == 0 or jp_c == 0:
                continue
            usa_entry_off = usa_cfc_sector * SECTOR + idx * 16
            _usa_s, usa_c = struct.unpack("<II", iso_header[usa_entry_off : usa_entry_off + 8])
            if usa_c == jp_c:
                continue

            jp_off = jp_cfc_sector * SECTOR + jp_s * SECTOR
            jp_sectors = (jp_c + SECTOR - 1) // SECTOR
            f.seek(write_sector * SECTOR)
            f.write(jp_data[jp_off : jp_off + jp_c])
            f.write(b"\x00" * (jp_sectors * SECTOR - jp_c))

            # Full JP entry: comp size, flags, decomp size. The flags high
            # half tells the game whether to run the racjin decompressor —
            # bank 324 is compressed in USA but raw in JP, so keeping USA
            # flags makes the game "decompress" raw ADPCM into garbage.
            f.seek(usa_entry_off)
            f.write(struct.pack("<IIII", write_sector - usa_cfc_sector, jp_c, jp_f, jp_d))

            write_sector += jp_sectors
            banks_patched += 1

        # CFC.DIG now extends to the end of the banks
        cfc_new_size = (write_sector - usa_cfc_sector) * SECTOR
        update_dir_entry(f, usa_cfc_entry, usa_cfc_sector, cfc_new_size)

    print(f"  {banks_patched} banks replaced")
    print(f"  CFC.DIG: {usa_cfc_size / 1024 / 1024:.0f} MB -> {cfc_new_size / 1024 / 1024:.0f} MB")

    # --- Step 3: Shift XA.PAK, cutscenes, DATA0 ---
    print(f"\n{'=' * 60}")
    print("Step 3: Shift XA.PAK, cutscenes, DATA0")
    print(f"{'=' * 60}")

    jp_xa_info = find_file_in_iso(jp_data[: 10 * 1024 * 1024], b"XA.PAK;1")
    usa_xa_info = find_file_in_iso(iso_header, b"XA.PAK;1")
    assert jp_xa_info is not None, "XA.PAK not found in JP ISO"
    assert usa_xa_info is not None, "XA.PAK not found in USA ISO"
    jp_xa_sz = jp_xa_info[1]

    with open(out_iso_path, "r+b") as f:
        # Full JP XA.PAK, immediately after the grown CFC.DIG
        f.seek(write_sector * SECTOR)
        src = jp_xa_info[0] * SECTOR
        remaining = jp_xa_sz
        while remaining > 0:
            chunk = min(remaining, 64 * 1024 * 1024)
            f.write(jp_data[src : src + chunk])
            src += chunk
            remaining -= chunk
        pad = (SECTOR - (jp_xa_sz % SECTOR)) % SECTOR
        if pad:
            f.write(b"\x00" * pad)
        update_dir_entry(f, usa_xa_info[2], write_sector, jp_xa_sz)
        write_sector += (jp_xa_sz + SECTOR - 1) // SECTOR
        print(f"  XA.PAK: {jp_xa_sz / 1024 / 1024:.0f} MB")

        # Cutscenes in retail order, each at its natural size
        for name in DSI_NAMES:
            needle = f"{name}.DSI;1".encode()
            usa_info = find_file_in_iso(iso_header, needle)
            jp_pos = jp_data.find(needle)
            if not usa_info or jp_pos < 0:
                continue

            sub_path = dsi_files.get(name) if dsi_files else None
            f.seek(write_sector * SECTOR)
            if sub_path:
                sz = os.path.getsize(sub_path)
                with open(sub_path, "rb") as sf:
                    shutil.copyfileobj(sf, f, 64 * 1024 * 1024)
                label = "subtitled"
            else:
                jp_entry = jp_pos - 33
                jp_sec = struct.unpack("<I", jp_data[jp_entry + 2 : jp_entry + 6])[0]
                sz = struct.unpack("<I", jp_data[jp_entry + 10 : jp_entry + 14])[0]
                f.write(jp_data[jp_sec * SECTOR : jp_sec * SECTOR + sz])
                label = "JP"
            pad = (SECTOR - (sz % SECTOR)) % SECTOR
            if pad:
                f.write(b"\x00" * pad)
            update_dir_entry(f, usa_info[2], write_sector, sz)
            write_sector += (sz + SECTOR - 1) // SECTOR
            print(f"  {name}: {sz / 1024 / 1024:.1f} MB ({label})")

        # DATA0 last, matching retail order
        if data0_info:
            f.seek(write_sector * SECTOR)
            f.write(data0_content)
            pad = (SECTOR - (len(data0_content) % SECTOR)) % SECTOR
            if pad:
                f.write(b"\x00" * pad)
            update_dir_entry(f, data0_info[2], write_sector, len(data0_content))
            write_sector += (len(data0_content) + SECTOR - 1) // SECTOR

        # Real PS2 drives hang on reads that run past the end of the image,
        # so drop any stale tail and leave a 1MB zero margin
        f.truncate(write_sector * SECTOR)
        f.seek(write_sector * SECTOR)
        f.write(b"\x00" * (512 * SECTOR))

    print(f"\n  Output: {out_iso_path} ({os.path.getsize(out_iso_path) / 1024 / 1024:.0f} MB)")


def do_full(
    usa_iso_path: str, jp_iso_path: str, out_iso_path: str, dump_mkv_dir: str | None = None
) -> None:
    """Full pipeline: burn English subtitles onto the JP cutscenes, then
    write the patched ISO with the subtitled DSIs at their natural sizes."""
    ffmpeg_bin = find_or_build_ffmpeg()
    if not ffmpeg_bin:
        print("  ERROR: ffmpeg with libass not available — cannot burn subtitles.")
        print("  Use `patch.py audio` if you only want audio undub without subtitles.")
        sys.exit(1)

    if dump_mkv_dir:
        os.makedirs(dump_mkv_dir, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("Burning subtitles onto cutscenes")
    print(f"{'=' * 60}")

    with open(jp_iso_path, "rb") as f:
        jp_header = f.read(10 * 1024 * 1024)

    with tempfile.TemporaryDirectory() as tmp:
        dsi_files: dict[str, str] = {}
        for name in DSI_NAMES:
            ass_path = os.path.join(SUBS_DIR, f"{name}.ass")
            if not os.path.exists(ass_path):
                continue
            with open(ass_path) as f:
                if "Dialogue:" not in f.read():
                    continue

            jp_info = find_file_in_iso(jp_header, f"{name}.DSI;1".encode())
            if not jp_info:
                continue
            with open(jp_iso_path, "rb") as f:
                f.seek(jp_info[0] * SECTOR)
                jp_dsi_bytes = f.read(jp_info[1])

            sub_dsi = build_subtitled_dsi(ffmpeg_bin, jp_dsi_bytes, ass_path)

            # Export MKV if requested
            if dump_mkv_dir and sub_dsi is not None:
                src = DSI.from_bytes(jp_dsi_bytes)
                audio = src.extract_audio()
                sub = DSI.from_bytes(sub_dsi)
                video_bytes = sub.extract_video()
                with tempfile.NamedTemporaryFile(suffix=".m2v", delete=False) as mf:
                    mf.write(video_bytes)
                    m2v_path = mf.name
                mkv_path = os.path.join(dump_mkv_dir, f"{name}.mkv")
                dump_mkv(ffmpeg_bin, m2v_path, audio, mkv_path)
                os.unlink(m2v_path)
                if os.path.exists(mkv_path):
                    print(f"    -> {mkv_path}")

            if sub_dsi is None:
                print(f"  {name}: subtitle burn failed, keeping audio-only")
                continue

            sub_path = os.path.join(tmp, f"{name}.dsi")
            with open(sub_path, "wb") as f:
                f.write(sub_dsi)
            dsi_files[name] = sub_path
            print(f"  {name}: subtitled ({len(sub_dsi) / 1024 / 1024:.1f} MB)")

        do_audio(usa_iso_path, jp_iso_path, out_iso_path, dsi_files=dsi_files)


# =============================================================================
# xdelta
# =============================================================================


def _find_xdelta() -> str | None:
    """Find xdelta3 binary."""
    xdelta = shutil.which("xdelta3") or shutil.which("xdelta")
    for p in ["/opt/homebrew/bin/xdelta3", "/usr/local/bin/xdelta3"]:
        if not xdelta and os.path.exists(p):
            xdelta = p
    return xdelta


def do_xdelta(args: list[str]) -> None:
    xdelta_bin = _find_xdelta()
    if not xdelta_bin:
        print("ERROR: xdelta3 not found. Install: brew install xdelta")
        sys.exit(1)
    usa_path, xdelta_path = args[0], args[1]
    out_path = args[2] if len(args) > 2 else "FMA2_Undub.iso"
    print("Applying xdelta patch...")
    subprocess.run([xdelta_bin, "-d", "-s", usa_path, xdelta_path, out_path])
    print(f"Done! {out_path}")


def generate_xdelta(usa_iso_path: str, out_iso_path: str) -> None:
    xdelta_bin = _find_xdelta()
    if not xdelta_bin:
        print("WARNING: xdelta3 not found")
        return
    xdelta_path = os.path.splitext(out_iso_path)[0] + ".xdelta"
    print("\nGenerating xdelta patch...")
    subprocess.run(
        [xdelta_bin, "-9", "-S", "djw", "-f", "-e", "-s", usa_iso_path, out_iso_path, xdelta_path],
        capture_output=True,
    )
    if os.path.exists(xdelta_path):
        print(f"  {xdelta_path} ({os.path.getsize(xdelta_path) / (1024 * 1024):.0f} MB)")


# =============================================================================
# CLI
# =============================================================================


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    skip_verify = "--skip-verify" in sys.argv
    want_xdelta = "--generate-xdelta" in sys.argv
    dump_mkv_dir: str | None = None

    # Parse --dump-mkv and collect positional args (excluding flag values)
    skip_next = False
    args: list[str] = []
    for i, a in enumerate(sys.argv[2:], start=2):
        if skip_next:
            skip_next = False
            continue
        if a == "--dump-mkv" and i + 1 < len(sys.argv):
            dump_mkv_dir = sys.argv[i + 1]
            skip_next = True
        elif not a.startswith("--"):
            args.append(a)

    print("Fullmetal Alchemist 2: Curse of the Crimson Elixir — Undub Patcher")
    print("=" * 60)

    if mode == "xdelta":
        do_xdelta(args)
        return

    if len(args) < 2:
        print(f"Usage: patch.py {mode} <usa_iso> <jp_iso> [output_iso]")
        sys.exit(1)

    usa_path, jp_path = args[0], args[1]
    out_path = args[2] if len(args) > 2 else "FMA2_Undub.iso"

    for path, label in [(usa_path, "USA ISO"), (jp_path, "JP ISO")]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}")
            sys.exit(1)

    if not skip_verify:
        if not verify_iso(usa_path, "USA", EXPECTED_HASHES["usa"]):
            sys.exit(1)
        if not verify_iso(jp_path, "JP", EXPECTED_HASHES["jp"]):
            sys.exit(1)

    if mode == "full":
        do_full(usa_path, jp_path, out_path, dump_mkv_dir=dump_mkv_dir)
    elif mode == "audio":
        do_audio(usa_path, jp_path, out_path)
    else:
        print(f"Unknown mode: {mode}. Use: full, audio, or xdelta")
        sys.exit(1)

    # Update PVD volume size — required for real PS2 hardware
    final = os.path.getsize(out_path)
    final_sectors = (final + SECTOR - 1) // SECTOR
    with open(out_path, "r+b") as f:
        f.seek(16 * SECTOR + 80)
        f.write(struct.pack("<I", final_sectors))
        f.write(struct.pack(">I", final_sectors))

    print(f"\nDone! {out_path} ({final:,} bytes / {final / 1024 / 1024:.0f} MB)")
    print("Load in PCSX2 or burn to disc for real PS2 hardware.")

    if want_xdelta:
        generate_xdelta(usa_path, out_path)


if __name__ == "__main__":
    main()
