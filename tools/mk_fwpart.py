#!/usr/bin/env python3
"""
Extract the firmware region for one PSID from a Mellanox/NVIDIA .mfa firmware archive.

An .mfa holds one full flash image per PSID. This pulls out a single image's *firmware*
region only, deliberately stopping short of the device-data area so that a flash written
with the result keeps the switch's own GUID/MAC/PSID.

Usage:
    python3 mk_fwpart.py --list <archive.mfa>
    python3 mk_fwpart.py <archive.mfa> <PSID> <output.bin> [--take 0x410000]

Examples:
    python3 mk_fwpart.py --list /etc/mlnx/fw-SPC2.mfa
    python3 mk_fwpart.py /etc/mlnx/fw-SPC2.mfa MT_0000000303 /tmp/fwpart.bin

No dependencies beyond the standard library; runs on the switch itself.

Archive format (determined by inspection, not documented by NVIDIA):
    magic "MFAR", then three xz streams
      stream 1  index: fixed-stride records of PSID + board name + description
      stream 2  small metadata block
      stream 3  payload: N consecutive full flash images, in index order
"""
import hashlib
import lzma
import re
import sys

SLOT_SIZE = 0x2000000           # one full 32 MB flash image per PSID
DEFAULT_TAKE = 0x410000         # firmware region only - never the device-data area
FS4_MAGIC = bytes.fromhex("4d544657abcdef00fade12345678dead")
PSID_RE = rb"[A-Z]{2,3}_?[0-9]{8,13}"


def load_index(raw):
    """Return (psid_list, payload_offset) for an MFA archive."""
    if raw[:4] != b"MFAR":
        sys.exit("error: not an MFA archive (missing 'MFAR' magic)")
    streams = [m.start() for m in re.finditer(b"\xfd7zXZ\x00", raw)]
    if len(streams) < 3:
        sys.exit(f"error: unexpected MFA layout ({len(streams)} xz streams, expected >= 3)")
    # LZMADecompressor stops cleanly at this stream's end marker and ignores the rest.
    index = lzma.LZMADecompressor().decompress(raw[streams[0]:])
    psids = [m.group().decode() for m in re.finditer(PSID_RE, index)]
    if not psids:
        sys.exit("error: no PSIDs found in archive index")
    return psids, index, streams[2]


def describe(index, psid):
    """Best-effort board name following a PSID record in the index blob."""
    pos = index.find(psid.encode())
    if pos < 0:
        return ""
    window = index[pos + len(psid): pos + len(psid) + 120]
    names = re.findall(rb"[ -~]{6,}", window)
    return names[0].decode() if names else ""


def extract(raw, payload_off, slot, take):
    """Decompress just enough of the payload to capture one slot's leading `take` bytes."""
    lo = slot * SLOT_SIZE
    hi = lo + take
    dec = lzma.LZMADecompressor()
    pos = 0
    buf = bytearray()
    data = raw[payload_off:]
    try:
        for i in range(0, len(data), 1 << 20):
            chunk = dec.decompress(data[i:i + (1 << 20)])
            if not chunk:
                continue
            start, end = pos, pos + len(chunk)
            if end > lo and start < hi:
                buf += chunk[max(0, lo - start):max(0, min(len(chunk), hi - start))]
            pos = end
            if pos >= hi:
                break
    except lzma.LZMAError as exc:
        # The payload stream may end without a clean marker; harmless if we already have
        # everything we asked for. The length check below is the real guard.
        print(f"note: decompressor stopped early ({exc})", file=sys.stderr)
    return bytes(buf)


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)

    if argv[0] == "--list":
        if len(argv) < 2:
            sys.exit("usage: mk_fwpart.py --list <archive.mfa>")
        raw = open(argv[1], "rb").read()
        psids, index, _ = load_index(raw)
        print(f"{len(psids)} images in {argv[1]}\n")
        print(f"{'slot':>4}  {'PSID':<16} board")
        for i, psid in enumerate(psids):
            print(f"{i:>4}  {psid:<16} {describe(index, psid)}")
        print("\nShell equivalent for slot N:")
        print("  tail -c +1729 <archive.mfa> | xz -dc 2>/dev/null \\")
        print(f"    | dd bs=65536 skip=$((N*{SLOT_SIZE // 65536})) count={DEFAULT_TAKE // 65536}"
              " iflag=fullblock of=/tmp/fwpart.bin")
        return

    if len(argv) < 3:
        sys.exit(__doc__)
    archive, psid, out = argv[0], argv[1], argv[2]
    take = DEFAULT_TAKE
    if "--take" in argv:
        take = int(argv[argv.index("--take") + 1], 0)

    raw = open(archive, "rb").read()
    psids, index, payload_off = load_index(raw)
    if psid not in psids:
        sys.exit(f"error: PSID {psid} not in archive. Run --list to see the {len(psids)} available.")
    slot = psids.index(psid)
    print(f"{psid} is image {slot} of {len(psids)}  ({describe(index, psid)})")

    buf = extract(raw, payload_off, slot, take)

    if len(buf) != take:
        sys.exit(f"FAILED: extracted 0x{len(buf):x} bytes, expected 0x{take:x}")
    if buf[:16] != FS4_MAGIC:
        sys.exit(f"FAILED: bad FS4 magic {buf[:16].hex()} - wrong slot or archive layout")

    with open(out, "wb") as fh:
        fh.write(buf)
    print(f"wrote {out}")
    print(f"  size   {len(buf)} (0x{len(buf):x})")
    print(f"  sha256 {hashlib.sha256(buf).hexdigest()}")
    print("\nThis is the FIRMWARE REGION ONLY. Write it at offset 0x0 and do not exceed")
    print(f"0x{take:x} - the device-data region at 0x1f00000+ must stay untouched.")


if __name__ == "__main__":
    main()
