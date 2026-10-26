# Recovering a Mellanox/NVIDIA Spectrum-2 switch from "Flash Recovery Mode"

How to bring back an SN3420 (and likely any Spectrum-2 switch) whose ASIC shows up as
`15b3:024e` and where every NVIDIA tool says **"No valid FS4 image found"** — without
NVIDIA support, without the Enterprise Portal, and without an OEM-only `flint`.

Recovered one SN3420 that had been written off as dead for four years. All 60 ports back.

> **This repository documents a procedure, not a binary.** No NVIDIA firmware is
> redistributed here. The firmware you need is already on your switch, or free to download
> from NVIDIA's own public GitHub. See [Getting the firmware](#4-get-the-correct-firmware-image).

---

## Does this apply to you?

You are probably in the right place if **all** of these are true:

```bash
# 1. The ASIC reports its hardware ID instead of its normal one
lspci -nn -d 15b3:
# 07:00.0 Memory controller [0580]: Mellanox Technologies MT53100 [Spectrum-2, Flash recovery mode] [15b3:024e]
#                                                                                                     ^^^^^^^^^
# healthy would be: Ethernet controller ... [15b3:cf6c]

# 2. Every tool refuses to talk to it
sudo flint -d <pci> q
# -E- Failed to query Device. No valid FS4 image found.

sudo mlxfwmanager --query -d <pci>
#   PSID:                 <- empty
#   Base MAC:     N/A
#   Status:       Failed to open device

# 3. BUT the flash itself answers
sudo mst start
sudo flint -d <pci> -ocr hw query
#   Type        MX25Lxxx
#   TotalSize   0x2000000
#   ...
```

Point 3 is the one that matters. **If `hw query` works, your flash chip is alive and this is
very likely recoverable.** The symptoms in 1 and 2 look identical to a factory-blank ASIC —
which genuinely does need NVIDIA — but they are not the same thing.

Typical OS-level symptom: `show interface status` is empty on SONiC, `syncd` won't start
because `mlnx-fw-manager.service` failed, and the kernel logs:

```
sxd_kernel: [error] Spectrum-2 on bus 0000:07:00.0 is in 'Flash Mode' status
sxd_kernel: [error] pci_register_driver() returned successfully but probe function failed
```

---

## Why this looks unfixable (and isn't)

An interrupted firmware burn can leave two independent defects:

1. **The 4-byte image signature at flash offset `0x0` is zeroed.** mstflint's own
   `invalidateOldFWImages()` writes exactly four zero bytes over the magic `MTFW` of an image
   it is retiring. A burn that dies after that step leaves the flash signature-less.
2. **Part of the firmware body is erased** and never rewritten.

Every MFT tool scans for that signature **first** and gives up if it is missing — *before* it
ever reads the device-data region. So the PSID comes back empty and the MAC reads `N/A`, and
the switch is indistinguishable from a chip that never had identity data at all.

In our case the manufacturing data was **100% intact** the entire time. It was simply hidden
behind a failed four-byte check.

That distinction decides everything:

| | What it needs |
|---|---|
| Manufacturing data intact | This guide. Public tools only. |
| Manufacturing data truly gone | `smg` / "flint for OEM" — NVIDIA only. Open a support case. |

---

## Recovery

> **Read this first.** You are writing to the flash of a live ASIC. Back up before each
> write. Never write above `0x410000` unless you have a verified copy of your device-data
> region. The single most common way to turn a recoverable switch into an unrecoverable one
> is overwriting `MFG_INFO` at `0x1FF8000`.

Replace `<pci>` with your address from `lspci` (e.g. `07:00.0`) throughout.

### 1. Recover your switch's identity first

The system EEPROM is a **separate i2c chip** from the ASIC flash, so it survives this failure.
This is also how you get a serial number when the pull-out tab is missing:

```bash
sudo decode-syseeprom        # SONiC
onie-syseeprom               # from ONIE
```

Record **Part Number**, **Serial Number** and **Base MAC**. You need the part number to pick
the right firmware.

### 2. Confirm the device data is still on the flash

FS4 keeps its device-data table (DTOC) at `flash_size − 0x1000`. On a 32 MB chip:

```bash
sudo flint -d <pci> -ocr rb 0x1fff000 0x100
```

**Look for `0x44544f43` first — that is `DTOC` in ASCII.** If it is there, your identity data
survived and you can continue. If that region is all `0xffffffff`, stop: you are in the
NVIDIA-only case.

### 3. Repair the 4-byte signature

```bash
sudo flint -d <pci> -ocr rb 0x0 0x40
# 0x00000000 0xabcdef00 0xfade1234 0x5678dead ...
#     ^^^^ should be 0x4d544657 ("MTFW")
```

If the first dword is zero and the next three are `abcdef00 fade1234 5678dead`, that is the
signature-invalidation fingerprint. NOR flash can't flip 0→1, so the whole 4 KB sector must
be rewritten:

```bash
sudo flint -d <pci> -ocr rb 0x0 0x1000 /tmp/sector0.bin                 # back up
sudo flint -d <pci> -ocr rb 0x1f00000 0x100000 /tmp/devdata-backup.bin  # back up device data

python3 tools/patch_signature.py /tmp/sector0.bin /tmp/sector0-fixed.bin

sudo flint -d <pci> -ocr wb /tmp/sector0-fixed.bin 0x0
sudo flint -d <pci> -ocr rb 0x0 0x10     # expect 0x4d544657 0xabcdef00 0xfade1234 0x5678dead
sudo flint -d <pci> -ocr v
```

**Copy `devdata-backup.bin` off the switch now.** It is the only irreplaceable thing here.

If `flint v` now succeeds, **you are done** — jump to step 6. If it fails at `BOOT2` or later,
your firmware body is damaged too; continue.

### 4. Get the correct firmware image

First identify your PSID from the part number. The trailing letter encodes the shipped OS:

| Part number | Board name in archive | PSID | SKU |
|---|---|---|---|
| `MSN3420-CB2F`  | `MSN3420-Cxxx_Ax`  | `MT_0000000301` | Onyx |
| `MSN3420-CB2FO` | `MSN3420-CxxxO_Ax` | `MT_0000000302` | ONIE |
| `MSN3420-CB2FC` | `MSN3420-CxxxC_Ax` | `MT_0000000303` | Cumulus |

**Getting this wrong matters.** List what your archive actually contains:

```bash
python3 tools/mk_fwpart.py --list /etc/mlnx/fw-SPC2.mfa
```

**Source of the archive.** If you run SONiC it is already on the switch at
`/etc/mlnx/fw-SPC2.mfa` — official SONiC Mellanox builds, master included, ship it. Otherwise
download it from NVIDIA's public GitHub (no login):

```
https://github.com/Mellanox/Spectrum-SDK-Drivers-SONiC-Bins/releases
  e.g. fw-2020.1148/fw-SPC2-rel-29_2020_1148-EVB.mfa
```

Extract **only the firmware region** for your PSID:

```bash
python3 tools/mk_fwpart.py /etc/mlnx/fw-SPC2.mfa MT_0000000303 /tmp/fwpart.bin
```

Or, with no Python, a pure shell equivalent once you know your slot index (`--list` prints it):

```bash
# slot 7 -> skip = 7 * 32MB / 64KB = 3584
tail -c +1729 /etc/mlnx/fw-SPC2.mfa | xz -dc 2>/dev/null \
  | dd bs=65536 skip=3584 count=65 iflag=fullblock of=/tmp/fwpart.bin
```

`iflag=fullblock` is **mandatory** — without it `dd` short-reads the pipe and silently
produces a truncated file.

For reference, `MT_0000000303` out of `fw-SPC2-rel-29_2020_1148-EVB.mfa` gives:

```
sha256  d861ea0d949023039366ae716dab6adc0cd6fd2a9a36fec3869107b7a2835538
size    4259840 (0x410000)
```

### 5. Write the firmware — and only the firmware

```bash
sudo systemctl stop syncd swss pmon     # SONiC
sudo mst start

for i in $(seq 0 64); do
  dd if=/tmp/fwpart.bin of=/tmp/chunk.bin bs=65536 skip=$i count=1 2>/dev/null
  sudo flint -d <pci> -ocr wb /tmp/chunk.bin $((i*65536)) || { echo "FAILED at chunk $i"; break; }
  echo "chunk $i/64 ok"
done
```

64 KB chunks rather than one 4 MB write: you get progress, and a failure leaves a known
resume offset.

> ### The write boundary is the whole point
>
> The firmware body ends around `0x4079bf`. Device data lives from `0x1F00000` up. Writing
> `0x0`–`0x410000` replaces the firmware and stops **31 MB short** of your MAC addresses.
>
> You will find advice online to `wb` the **full 32 MB** image at `0x0`, claiming it "leaves
> DTOC and MFG_INFO untouched further up the flash space". **That is false.** A 32 MB write
> from `0x0` covers `0x0`–`0x1FFFFFF`, which includes `MFG_INFO` at `0x1FF8000` and `DTOC` at
> `0x1FFF000`. It destroys exactly what makes your switch recoverable.
>
> Likewise `--allow_psid_change` has nothing to do with a broken signature — it permits
> overwriting an intact PSID. Don't.

### 6. Verify, then power cycle

```bash
sudo flint -d <pci> -ocr v     # want: "FW image verification succeeded. Image is bootable."
sudo flint -d <pci> -ocr q     # want: your PSID, a FW version, your real Base MAC
```

**Only power-cycle once `q` shows your original MAC.** Pull both AC cords for 30 seconds — a
warm reboot is not enough, the ASIC must re-enumerate on PCI.

```bash
lspci -nn -d 15b3:
# 07:00.0 Ethernet controller [0200]: ... [Spectrum-2] [15b3:cf6c] (rev a0)
show interface status
```

---

## Flash memory map (Spectrum-2, 32 MB)

```
0x0000000  image signature "MTFW" + HW pointers
0x0000500  TOOLS_AREA
0x0001000  BOOT2
0x0004000  ITOC  (firmware section table)
0x0007100  MAIN_CODE ... firmware body ends ~0x04079bf
---------- safe write boundary: 0x0410000 ----------
0x1F00000  FW_NV_LOG   }
0x1F20000  NV_DATA     }
0x1F40000  NV_DATA     }  device data — NEVER write without a verified backup
0x1F60000  DEV_INFO    }
0x1F70000  DEV_INFO    }
0x1FF8000  MFG_INFO  (PSID, GUID, MAC)   <- the irreplaceable 320 bytes
0x1FF8140  VPD_R0
0x1FFF000  DTOC  (device-data table)
```

---

## Back up now, so next time is ten minutes

The firmware is **not** worth backing up — it is byte-reproducible from NVIDIA's public
archive. Your **device data** is irreplaceable. On a healthy switch:

```bash
# no -ocr once firmware is running: it can hang live firmware
sudo flint -d <pci> rb 0x1f00000 0x100000 /host/backup-devdata.bin   # <- the critical one
sudo flint -d <pci> ri /host/backup-golden.bin                       # golden image
sudo flint -d <pci> rb 0x0 0x1000 /host/backup-sector0.bin
sudo decode-syseeprom > /host/backup-syseeprom.txt
sha256sum /host/backup-* > /host/backup-SHA256SUMS.txt
```

Store them **off the switch**. See [docs/backup-restore.md](docs/backup-restore.md) for the
restore paths, including the device-data restore.

---

## Repository contents

| Path | What |
|---|---|
| [`README.md`](README.md) | This guide |
| [`docs/diagnosis.md`](docs/diagnosis.md) | Full diagnostic walkthrough with evidence and mstflint source references |
| [`docs/backup-restore.md`](docs/backup-restore.md) | Backup procedure and four restore cases |
| [`tools/mk_fwpart.py`](tools/mk_fwpart.py) | Lists an `.mfa` archive and extracts the firmware region for one PSID |
| [`tools/patch_signature.py`](tools/patch_signature.py) | Restores the 4-byte `MTFW` signature in a dumped sector |

Both tools are plain Python 3, no dependencies, and run on the switch itself.

---

## Scope and caveats

- Verified on **one** SN3420 (`MSN3420-CB2FC`, Spectrum-2, 32 MB Macronix flash) running
  SONiC master, firmware 29.2020.1148.
- The addresses here are **specific to Spectrum-2 with a 32 MB flash.** The DTOC location is
  computed as `flash_size − 0x1000`, so it moves on a different flash size. Verify with
  `flint -ocr hw query` before trusting any offset.
- The device-data restore path (Case C in the backup doc) is **untested** — it never came up.
- Serial numbers and MACs in the docs are redacted. Substitute your own.
- Nothing here is endorsed by or affiliated with NVIDIA. You are writing to your own
  hardware's flash at your own risk.

## Credits

Diagnosis worked out by reading the open-source
[mstflint](https://github.com/Mellanox/mstflint) sources — every structural claim in
[`docs/diagnosis.md`](docs/diagnosis.md) cites the file and line it came from. Thanks to
NVIDIA for keeping mstflint and the SONiC firmware archives public; that is the only reason
this was solvable from outside.

## License

[MIT](LICENSE) for the documentation and tools in this repository. No NVIDIA firmware,
binaries, or documentation is included or redistributed here.
