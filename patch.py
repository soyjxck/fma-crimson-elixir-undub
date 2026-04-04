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

import struct
import os
import sys
import shutil
import subprocess
import tempfile

from racjin import compress, decompress

from lib.constants import (
    SECTOR, DSI_NAMES, SCEI_BANK_INDICES, EXPECTED_MD5,
    CFC_TRACK_TABLE_DIR_OFFSET, SUBS_DIR,
)
from lib.iso import find_file_entry, update_dir_entry, verify_md5
from lib.ffmpeg import find_or_build_ffmpeg
from lib.video import build_subtitled_dsi, dump_mkv


# =============================================================================
# Core patching
# =============================================================================

def do_audio(usa_iso_path, jp_iso_path, out_iso_path):
    """Audio-only undub: JP XA.PAK + JP DSI + JP combat banks, no subtitles."""
    print("Reading JP ISO...")
    with open(jp_iso_path, 'rb') as f:
        jp_data = f.read()
    print(f"  USA: {os.path.getsize(usa_iso_path):,} bytes")
    print(f"  JP:  {len(jp_data):,} bytes")

    print(f"\nCopying USA ISO as base...")
    shutil.copy2(usa_iso_path, out_iso_path)

    with open(out_iso_path, 'rb') as f:
        iso_header = f.read(10 * 1024 * 1024)

    usa_cfc_info = find_file_entry(iso_header, b'CFC.DIG;1')
    usa_cfc_sector = usa_cfc_info[1]

    jp_cfc_info = find_file_entry(jp_data[:10 * 1024 * 1024], b'CFC.DIG;1')
    jp_cfc_sector = jp_cfc_info[1]

    # --- Step 1: Patch XA track offset table ---
    print(f"\n{'='*60}")
    print("Step 1: XA track offset table")
    print(f"{'='*60}")

    for label, path, cfc_sec in [("USA", usa_iso_path, usa_cfc_sector),
                                  ("JP", jp_iso_path, jp_cfc_sector)]:
        with open(path, 'rb') as f:
            f.seek(cfc_sec * SECTOR + CFC_TRACK_TABLE_DIR_OFFSET)
            us, uc, uf, ud = struct.unpack('<IIII', f.read(16))
            f.seek(cfc_sec * SECTOR + us * SECTOR)
            raw = f.read(uc)
        decompressed = decompress(raw, ud)
        if label == "USA":
            usa_decomp = bytearray(decompressed)
        else:
            jp_decomp = decompressed

    patched = bytearray(usa_decomp)
    changed = 0
    for t in range(2016):
        eoff = 0x30 + t * 0x10
        if jp_decomp[eoff:eoff+8] != usa_decomp[eoff:eoff+8]:
            patched[eoff:eoff+8] = jp_decomp[eoff:eoff+8]
            changed += 1

    cfc2_comp = compress(bytes(patched))
    cfc2_sectors = (len(cfc2_comp) + SECTOR - 1) // SECTOR
    print(f"  Patched {changed}/2016 track offsets")

    # --- Step 2: Write compacted ISO layout ---
    print(f"\n{'='*60}")
    print("Step 2: Write compacted ISO layout")
    print(f"{'='*60}")

    write_sector = 92828

    # Track table
    cfc2_rel_sector = write_sector - usa_cfc_sector
    with open(out_iso_path, 'r+b') as f:
        f.seek(write_sector * SECTOR)
        f.write(cfc2_comp)
        f.write(b'\x00' * (cfc2_sectors * SECTOR - len(cfc2_comp)))
        f.seek(usa_cfc_sector * SECTOR + CFC_TRACK_TABLE_DIR_OFFSET)
        f.write(struct.pack('<I', cfc2_rel_sector))
        f.write(struct.pack('<I', len(cfc2_comp)))
    write_sector += cfc2_sectors
    print(f"  Track table: {cfc2_sectors} sectors")

    # DSI cutscenes (full JP, no truncation)
    for name in DSI_NAMES:
        needle = f'{name}.DSI;1'.encode()
        usa_info = find_file_entry(iso_header, needle)
        jp_pos = jp_data.find(needle)
        if not usa_info or jp_pos < 0:
            continue
        jp_entry = jp_pos - 33
        jp_sec = struct.unpack('<I', jp_data[jp_entry+2:jp_entry+6])[0]
        jp_sz = struct.unpack('<I', jp_data[jp_entry+10:jp_entry+14])[0]

        with open(out_iso_path, 'r+b') as f:
            f.seek(write_sector * SECTOR)
            f.write(jp_data[jp_sec*SECTOR:jp_sec*SECTOR+jp_sz])
            pad = (SECTOR - (jp_sz % SECTOR)) % SECTOR
            if pad:
                f.write(b'\x00' * pad)
            update_dir_entry(f, usa_info[0], write_sector, jp_sz)

        file_sectors = (jp_sz + SECTOR - 1) // SECTOR
        print(f"  {name}: {jp_sz/1024/1024:.1f} MB")
        write_sector += file_sectors

    # DATA0
    data0_info = find_file_entry(iso_header, b'DATA0')
    if data0_info:
        with open(out_iso_path, 'rb') as f:
            f.seek(data0_info[1] * SECTOR)
            data0_content = f.read(data0_info[2])
        with open(out_iso_path, 'r+b') as f:
            f.seek(write_sector * SECTOR)
            f.write(data0_content)
            update_dir_entry(f, data0_info[0], write_sector, data0_info[2])
        write_sector += (data0_info[2] + SECTOR - 1) // SECTOR

    # Full JP XA.PAK
    jp_xa_info = find_file_entry(jp_data[:10*1024*1024], b'XA.PAK;1')
    usa_xa_info = find_file_entry(iso_header, b'XA.PAK;1')
    jp_xa_sz = jp_xa_info[2]

    with open(out_iso_path, 'r+b') as f:
        f.seek(write_sector * SECTOR)
        remaining = jp_xa_sz
        src = jp_xa_info[1] * SECTOR
        while remaining > 0:
            chunk = min(remaining, 64 * 1024 * 1024)
            f.write(jp_data[src:src+chunk])
            src += chunk
            remaining -= chunk
        pad = (SECTOR - (jp_xa_sz % SECTOR)) % SECTOR
        if pad:
            f.write(b'\x00' * pad)
        update_dir_entry(f, usa_xa_info[0], write_sector, jp_xa_sz)

    xa_end = write_sector + (jp_xa_sz + SECTOR - 1) // SECTOR
    print(f"  XA.PAK: {jp_xa_sz/1024/1024:.0f} MB")

    # --- Step 3: Combat bark SCEI banks ---
    print(f"\n{'='*60}")
    print("Step 3: Combat bark SCEI banks")
    print(f"{'='*60}")

    banks_patched = 0
    last_cfc_sector = 92828 + cfc2_sectors

    with open(out_iso_path, 'r+b') as f:
        for idx in SCEI_BANK_INDICES:
            jp_s, jp_c, jp_f, jp_d = struct.unpack('<IIII',
                jp_data[jp_cfc_sector*SECTOR + idx*16:jp_cfc_sector*SECTOR + idx*16 + 16])
            if jp_s == 0 or jp_c == 0:
                continue
            usa_s, usa_c = struct.unpack('<II',
                iso_header[usa_cfc_sector*SECTOR + idx*16:usa_cfc_sector*SECTOR + idx*16 + 8])
            if usa_c == jp_c:
                continue

            jp_raw = jp_data[jp_cfc_sector*SECTOR + jp_s*SECTOR:
                             jp_cfc_sector*SECTOR + jp_s*SECTOR + jp_c]

            f.seek(0, 2)
            pos = f.tell()
            new_abs_sector = (pos + SECTOR - 1) // SECTOR
            new_rel_sector = new_abs_sector - usa_cfc_sector
            jp_sectors = (jp_c + SECTOR - 1) // SECTOR

            f.seek(new_abs_sector * SECTOR)
            f.write(jp_raw)
            f.write(b'\x00' * (jp_sectors * SECTOR - jp_c))

            f.seek(usa_cfc_sector * SECTOR + idx * 16)
            f.write(struct.pack('<I', new_rel_sector))
            f.write(struct.pack('<I', jp_c))
            f.seek(usa_cfc_sector * SECTOR + idx * 16 + 12)
            f.write(struct.pack('<I', jp_d))

            cfc_end = new_abs_sector + jp_sectors
            if cfc_end > last_cfc_sector:
                last_cfc_sector = cfc_end

            banks_patched += 1

    print(f"  {banks_patched} banks replaced")

    # Update CFC.DIG size
    cfc_new_size = (last_cfc_sector - usa_cfc_sector) * SECTOR
    with open(out_iso_path, 'r+b') as f:
        update_dir_entry(f, usa_cfc_info[0], usa_cfc_sector, cfc_new_size)

    print(f"\n  Output: {out_iso_path} ({os.path.getsize(out_iso_path)/1024/1024:.0f} MB)")
    return jp_data, jp_cfc_sector


def do_full(usa_iso_path, jp_iso_path, out_iso_path, dump_mkv_dir=None):
    """Full pipeline: audio-only patching + burned English subtitles on cutscenes."""
    jp_data, jp_cfc_sector = do_audio(usa_iso_path, jp_iso_path, out_iso_path)

    if dump_mkv_dir:
        os.makedirs(dump_mkv_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("Step 4: Burn subtitles onto cutscenes")
    print(f"{'='*60}")

    ffmpeg_bin = find_or_build_ffmpeg()
    if not ffmpeg_bin:
        print("  WARNING: ffmpeg with libass not available — skipping subtitles")
        return

    with open(out_iso_path, 'rb') as f:
        iso_header = f.read(10 * 1024 * 1024)

    for name in DSI_NAMES:
        ass_path = os.path.join(SUBS_DIR, f'{name}.ass')
        if not os.path.exists(ass_path):
            continue
        with open(ass_path) as f:
            if 'Dialogue:' not in f.read():
                continue

        # Find current DSI location in our patched ISO
        usa_info = find_file_entry(iso_header, f'{name}.DSI;1'.encode())
        if not usa_info:
            continue
        cur_sector = usa_info[1]
        cur_size = usa_info[2]

        # Build subtitled DSI from the JP DSI currently in our ISO
        with open(out_iso_path, 'rb') as f:
            f.seek(cur_sector * SECTOR)
            jp_dsi_bytes = f.read(cur_size)

        sub_dsi = build_subtitled_dsi(ffmpeg_bin, jp_dsi_bytes, ass_path)

        # Export MKV if requested
        if dump_mkv_dir and sub_dsi is not None:
            from dsi_muxer import DSI as _DSI
            _src = _DSI.from_bytes(jp_dsi_bytes)
            _audio = _src.extract_audio()
            _sub = _DSI.from_bytes(sub_dsi)
            _video_bytes = _sub.extract_video()
            with tempfile.NamedTemporaryFile(suffix='.m2v', delete=False) as _mf:
                _mf.write(_video_bytes)
                _m2v_path = _mf.name
            mkv_path = os.path.join(dump_mkv_dir, f'{name}.mkv')
            dump_mkv(ffmpeg_bin, _m2v_path, _audio, mkv_path)
            os.unlink(_m2v_path)
            if os.path.exists(mkv_path):
                print(f"    -> {mkv_path}")

        if sub_dsi is None:
            print(f"  {name}: subtitle burn failed, keeping audio-only")
            continue

        if len(sub_dsi) <= cur_size:
            # Fits in place
            with open(out_iso_path, 'r+b') as f:
                f.seek(cur_sector * SECTOR)
                f.write(sub_dsi)
                f.write(b'\x00' * (cur_size - len(sub_dsi)))
                update_dir_entry(f, usa_info[0], cur_sector, len(sub_dsi))
            print(f"  {name}: subtitled ({len(sub_dsi)/1024/1024:.1f} MB)")
        else:
            # Append at end
            with open(out_iso_path, 'r+b') as f:
                f.seek(0, 2)
                new_sector = (f.tell() + SECTOR - 1) // SECTOR
                f.seek(new_sector * SECTOR)
                f.write(sub_dsi)
                pad = (SECTOR - (len(sub_dsi) % SECTOR)) % SECTOR
                if pad:
                    f.write(b'\x00' * pad)
                update_dir_entry(f, usa_info[0], new_sector, len(sub_dsi))
            print(f"  {name}: subtitled, relocated ({len(sub_dsi)/1024/1024:.1f} MB)")


# =============================================================================
# xdelta
# =============================================================================

def _find_xdelta():
    """Find xdelta3 binary."""
    xdelta = shutil.which('xdelta3') or shutil.which('xdelta')
    for p in ['/opt/homebrew/bin/xdelta3', '/usr/local/bin/xdelta3']:
        if not xdelta and os.path.exists(p):
            xdelta = p
    return xdelta


def do_xdelta(args):
    xdelta_bin = _find_xdelta()
    if not xdelta_bin:
        print("ERROR: xdelta3 not found. Install: brew install xdelta")
        sys.exit(1)
    usa_path, xdelta_path = args[0], args[1]
    out_path = args[2] if len(args) > 2 else 'FMA2_Undub.iso'
    print("Applying xdelta patch...")
    subprocess.run([xdelta_bin, '-d', '-s', usa_path, xdelta_path, out_path])
    print(f"Done! {out_path}")


def generate_xdelta(usa_iso_path, out_iso_path):
    xdelta_bin = _find_xdelta()
    if not xdelta_bin:
        print("WARNING: xdelta3 not found")
        return
    xdelta_path = os.path.splitext(out_iso_path)[0] + '.xdelta'
    print("\nGenerating xdelta patch...")
    subprocess.run([xdelta_bin, '-9', '-S', 'djw', '-f', '-e', '-s',
                    usa_iso_path, out_iso_path, xdelta_path], capture_output=True)
    if os.path.exists(xdelta_path):
        print(f"  {xdelta_path} ({os.path.getsize(xdelta_path) / (1024 * 1024):.0f} MB)")


# =============================================================================
# CLI
# =============================================================================

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    mode = sys.argv[1]
    skip_verify = '--skip-verify' in sys.argv
    want_xdelta = '--generate-xdelta' in sys.argv
    dump_mkv_dir = None

    # Parse --dump-mkv and collect positional args (excluding flag values)
    skip_next = False
    args = []
    for i, a in enumerate(sys.argv[2:], start=2):
        if skip_next:
            skip_next = False
            continue
        if a == '--dump-mkv' and i + 1 < len(sys.argv):
            dump_mkv_dir = sys.argv[i + 1]
            skip_next = True
        elif not a.startswith('--'):
            args.append(a)

    print("Fullmetal Alchemist 2: Curse of the Crimson Elixir — Undub Patcher")
    print("=" * 60)

    if mode == 'xdelta':
        do_xdelta(args)
        return

    if len(args) < 2:
        print(f"Usage: patch.py {mode} <usa_iso> <jp_iso> [output_iso]")
        sys.exit(1)

    usa_path, jp_path = args[0], args[1]
    out_path = args[2] if len(args) > 2 else 'FMA2_Undub.iso'

    for path, label in [(usa_path, 'USA ISO'), (jp_path, 'JP ISO')]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}")
            sys.exit(1)

    if not skip_verify:
        if not verify_md5(usa_path, 'USA', EXPECTED_MD5['usa']):
            sys.exit(1)
        if not verify_md5(jp_path, 'JP', EXPECTED_MD5['jp']):
            sys.exit(1)

    if mode == 'full':
        do_full(usa_path, jp_path, out_path, dump_mkv_dir=dump_mkv_dir)
    elif mode == 'audio':
        do_audio(usa_path, jp_path, out_path)
    else:
        print(f"Unknown mode: {mode}. Use: full, audio, or xdelta")
        sys.exit(1)

    final = os.path.getsize(out_path)
    print(f"\nDone! {out_path} ({final:,} bytes / {final/1024/1024:.0f} MB)")
    print("Load in PCSX2 — use memory card saves, not save states.")

    if want_xdelta:
        generate_xdelta(usa_path, out_path)


if __name__ == '__main__':
    main()
