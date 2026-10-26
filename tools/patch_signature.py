#!/usr/bin/env python3
"""
Restore the 4-byte FS4 image signature ("MTFW") in a dumped flash sector.

A firmware burn that is interrupted after mstflint's invalidateOldFWImages() leaves the
first dword of the image magic zeroed and the rest intact. NOR flash cannot flip 0 bits
back to 1, so the fix is to rewrite the whole 4 KB sector with the magic restored.

Usage:
    sudo flint -d <pci> -ocr rb 0x0 0x1000 /tmp/sector0.bin
    python3 patch_signature.py /tmp/sector0.bin /tmp/sector0-fixed.bin
    sudo flint -d <pci> -ocr wb /tmp/sector0-fixed.bin 0x0
    sudo flint -d <pci> -ocr rb 0x0 0x10     # expect 0x4d544657 0xabcdef00 ...

The script refuses to produce output unless the input looks exactly like the expected
damage, so it cannot be pointed at an unrelated sector by mistake.

The full FS4 magic is {0x4D544657 "MTFW", 0xABCDEF00, 0xFADE1234, 0x5678DEAD}
  - mstflint, mlxfwops/lib/fw_ops.cpp
"""
import sys

SECTOR = 0x1000
# Bytes 4..16 of the magic, in each possible on-disk order.
TAIL_BE = bytes.fromhex("abcdef00fade12345678dead")
TAIL_LE = bytes.fromhex("00efcdab3412defaadde7856")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]

    data = bytearray(open(src, "rb").read())

    if len(data) != SECTOR:
        sys.exit(f"REFUSING: input is {len(data)} bytes, expected {SECTOR} "
                 f"(dump exactly one sector: rb 0x0 0x1000)")

    if data[0:4] == b"MTFW" or data[0:4] == b"WFTM":
        sys.exit("Nothing to do: the signature is already present. "
                 "If flint still fails, the damage is elsewhere - run 'flint -ocr v'.")

    if data[0:4] != b"\x00\x00\x00\x00":
        sys.exit(f"REFUSING: first dword is {data[0:4].hex()}, expected 00000000. "
                 "This is not the signature-invalidation fingerprint.")

    if data[4:16] == TAIL_BE:
        data[0:4] = b"MTFW"
        order = "MTFW"
    elif data[4:16] == TAIL_LE:
        data[0:4] = b"WFTM"
        order = "WFTM (byte-swapped dump)"
    else:
        sys.exit(f"REFUSING: bytes 4..16 are {data[4:16].hex()}, which is neither "
                 f"{TAIL_BE.hex()} nor {TAIL_LE.hex()}. The rest of the magic is damaged "
                 "too, so restoring 4 bytes will not help.")

    with open(dst, "wb") as fh:
        fh.write(bytes(data))

    print(f"patched: wrote {order} at offset 0 -> {dst}")
    print("Back up the original before writing, then:")
    print(f"  sudo flint -d <pci> -ocr wb {dst} 0x0")
    print("  sudo flint -d <pci> -ocr rb 0x0 0x10")
    print("  sudo flint -d <pci> -ocr v")


if __name__ == "__main__":
    main()
