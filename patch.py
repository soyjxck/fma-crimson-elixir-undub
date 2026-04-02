#!/usr/bin/env python3
"""
FMA2 Undub Patcher
==================
Replaces English audio with Japanese audio in Fullmetal Alchemist 2:
Curse of the Crimson Elixir (USA) PS2.

Usage: python3 patch.py <usa_iso> <jp_iso> [output_iso]
       python3 patch.py --generate-xdelta <usa_iso> <jp_iso> [output_iso]
"""

import struct
import os
import sys
import shutil
import hashlib
import subprocess

from racjin import compress, decompress

SECTOR = 2048

EXPECTED_MD5 = {
    "usa": "2e79a69434561557dd0eaa9061d62eed",
    "jp":  "6804b82a9eb8d6a1e2d85a25683ec89d",
}


def find_file_entry(iso_data, filename):
    """Find a file's directory entry offset, sector, and size in ISO header data."""
    needle = filename.encode() if isinstance(filename, str) else filename
    pos = iso_data.find(needle)
    if pos < 0:
        return None
    entry = pos - 33
    sector = struct.unpack('<I', iso_data[entry + 2:entry + 6])[0]
    size = struct.unpack('<I', iso_data[entry + 10:entry + 14])[0]
    return entry, sector, size


def update_dir_entry(f, entry_offset, sector, size):
    """Update an ISO9660 directory entry's sector and size (both LE and BE)."""
    f.seek(entry_offset + 2)
    f.write(struct.pack('<I', sector))
    f.write(struct.pack('>I', sector))
    f.seek(entry_offset + 10)
    f.write(struct.pack('<I', size))
    f.write(struct.pack('>I', size))


def verify_md5(path, label, expected):
    """Verify ISO MD5 hash."""
    print(f"  Verifying {label}...", end=" ", flush=True)
    md5 = hashlib.md5()
    with open(path, 'rb') as f:
        while chunk := f.read(64 * 1024 * 1024):
            md5.update(chunk)
    digest = md5.hexdigest()
    if digest == expected:
        print("OK")
        return True
    print(f"MISMATCH (got {digest})")
    return False


def do_undub(usa_iso_path, jp_iso_path, out_iso_path):
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

    # Find JP CFC.DIG sector (differs from USA!)
    jp_cfc_info = find_file_entry(jp_data[:10 * 1024 * 1024], b'CFC.DIG;1')
    jp_cfc_sector = jp_cfc_info[1]

    # =========================================================================
    # Step 1: Patch CFC entry 2 — XA track offset table
    # =========================================================================
    print(f"\n{'='*60}")
    print("Step 1: XA track offset table (CFC entry at dir offset 0x30)")
    print(f"{'='*60}")

    # Read and decompress the track table from both ISOs
    for label, path, cfc_sec in [("USA", usa_iso_path, usa_cfc_sector),
                                  ("JP", jp_iso_path, jp_cfc_sector)]:
        with open(path, 'rb') as f:
            f.seek(cfc_sec * SECTOR + 0x30)
            us, uc, uf, ud = struct.unpack('<IIII', f.read(16))
            f.seek(cfc_sec * SECTOR + us * SECTOR)
            raw = f.read(uc)
        decompressed = decompress(raw, ud)
        if label == "USA":
            usa_decomp = bytearray(decompressed)
        else:
            jp_decomp = decompressed

    # Surgical patch: replace only track offset+size (bytes 0-7 of each 16-byte entry)
    # Keep all USA metadata (bytes 8-15) to avoid playback issues
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
    print(f"  Compressed: {len(cfc2_comp):,} bytes ({cfc2_sectors} sectors)")

    # =========================================================================
    # Step 2: Write compacted ISO layout
    # =========================================================================
    print(f"\n{'='*60}")
    print("Step 2: Write compacted ISO layout")
    print(f"{'='*60}")

    # Layout: CFC.DIG (original) | CFC[2] track table | DSI | DATA0 | XA.PAK
    write_sector = 92828  # start of free space after original CFC.DIG data

    # --- Track table ---
    cfc2_abs_sector = write_sector
    cfc2_rel_sector = cfc2_abs_sector - usa_cfc_sector
    with open(out_iso_path, 'r+b') as f:
        f.seek(cfc2_abs_sector * SECTOR)
        f.write(cfc2_comp)
        f.write(b'\x00' * (cfc2_sectors * SECTOR - len(cfc2_comp)))
        f.seek(usa_cfc_sector * SECTOR + 0x30)
        f.write(struct.pack('<I', cfc2_rel_sector))
        f.write(struct.pack('<I', len(cfc2_comp)))
    write_sector += cfc2_sectors
    print(f"  Track table: sector {cfc2_abs_sector} ({cfc2_sectors} sectors)")

    # --- DSI cutscenes (full JP, no truncation) ---
    dsi_names = ['MV00','MV01','MV02','MV03','MV04','MV05',
                 'MV06','MV07','MV08','MV09','MV10','MV11']
    for name in dsi_names:
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
        print(f"  {name}: sector {write_sector} ({jp_sz/1024/1024:.1f} MB)")
        write_sector += file_sectors

    # --- DATA0 ---
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

    # --- Full JP XA.PAK ---
    jp_xa_info = find_file_entry(jp_data[:10*1024*1024], b'XA.PAK;1')
    usa_xa_info = find_file_entry(iso_header, b'XA.PAK;1')
    jp_xa_sec = jp_xa_info[1]
    jp_xa_sz = jp_xa_info[2]

    with open(out_iso_path, 'r+b') as f:
        f.seek(write_sector * SECTOR)
        remaining = jp_xa_sz
        src = jp_xa_sec * SECTOR
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
    print(f"  XA.PAK: sector {write_sector} ({jp_xa_sz/1024/1024:.0f} MB)")

    # --- Update CFC.DIG size ---
    cfc_new_size = (cfc2_rel_sector + cfc2_sectors) * SECTOR
    with open(out_iso_path, 'r+b') as f:
        update_dir_entry(f, usa_cfc_info[0], usa_cfc_sector, cfc_new_size)

    # --- Truncate ---
    with open(out_iso_path, 'r+b') as f:
        f.truncate(xa_end * SECTOR)

    # =========================================================================
    # Summary
    # =========================================================================
    final_size = os.path.getsize(out_iso_path)
    print(f"\n{'='*60}")
    print("Undub complete!")
    print(f"{'='*60}")
    print(f"  Output:    {out_iso_path}")
    print(f"  Size:      {final_size:,} bytes ({final_size/1024/1024:.0f} MB)")
    print(f"  XA tracks: {changed} offsets patched")
    print(f"  XA.PAK:    {jp_xa_sz/1024/1024:.0f} MB (full JP)")
    print(f"  Cutscenes: 12 JP DSI files (full, no truncation)")


def generate_xdelta(usa_iso_path, out_iso_path):
    """Generate xdelta patch file."""
    xdelta_bin = shutil.which('xdelta3') or shutil.which('xdelta')
    for p in ['/opt/homebrew/bin/xdelta3', '/usr/local/bin/xdelta3']:
        if not xdelta_bin and os.path.exists(p):
            xdelta_bin = p
    if not xdelta_bin:
        print("WARNING: xdelta3 not found — install with: brew install xdelta")
        return
    xdelta_path = os.path.splitext(out_iso_path)[0] + '.xdelta'
    print(f"\nGenerating xdelta patch...")
    subprocess.run([xdelta_bin, '-9', '-S', 'djw', '-f', '-e', '-s',
                    usa_iso_path, out_iso_path, xdelta_path], capture_output=True)
    if os.path.exists(xdelta_path):
        print(f"  {xdelta_path} ({os.path.getsize(xdelta_path) / (1024 * 1024):.0f} MB)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    skip_verify = '--skip-verify' in sys.argv
    want_xdelta = '--generate-xdelta' in sys.argv

    if len(args) < 2:
        print("Fullmetal Alchemist 2 — Undub Patcher")
        print("Usage: python3 patch.py [options] <usa_iso> <jp_iso> [output_iso]")
        print()
        print("Options:")
        print("  --skip-verify       Skip MD5 hash verification")
        print("  --generate-xdelta   Also create an xdelta patch file")
        sys.exit(1)

    usa_path = args[0]
    jp_path = args[1]
    out_path = args[2] if len(args) > 2 else 'FMA2_Undub.iso'

    print("Fullmetal Alchemist 2: Curse of the Crimson Elixir — Undub Patcher")
    print("=" * 60)

    for path, label in [(usa_path, 'USA ISO'), (jp_path, 'JP ISO')]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}")
            sys.exit(1)

    if not skip_verify:
        if not verify_md5(usa_path, 'USA', EXPECTED_MD5['usa']):
            print("Use --skip-verify to bypass hash check")
            sys.exit(1)
        if not verify_md5(jp_path, 'JP', EXPECTED_MD5['jp']):
            print("Use --skip-verify to bypass hash check")
            sys.exit(1)

    do_undub(usa_path, jp_path, out_path)

    if want_xdelta:
        generate_xdelta(usa_path, out_path)


if __name__ == '__main__':
    main()
