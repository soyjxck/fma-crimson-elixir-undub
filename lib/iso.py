"""ISO9660 helpers."""

import struct
import hashlib


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
