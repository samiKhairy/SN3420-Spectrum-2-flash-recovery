# Full diagnostic walkthrough

The evidence behind [the recovery guide](../README.md), step by step, with the mstflint
source reference for every structural claim. Read this if you want to know *why* the
procedure is what it is — or if your symptoms differ and you need to adapt it.

Identifiers from the recovered unit are redacted (`MT21XXXXXXXX`, `1C:34:DA:XX:XX:XX`).
Model-level values such as PSIDs and the firmware sha256 are not unit-specific and are shown
in full.

**Unit under test:** SN3420, `MSN3420-CB2FC`, Spectrum-2 at PCI `07:00.0`, 32 MB Macronix
flash, SONiC `master.1254596`, firmware 29.2020.1148 after recovery.

---

## Step 1 — Is the ASIC in recovery mode?

```bash
lspci -nn -d 15b3:
07:00.0 Memory controller [0580]: Mellanox Technologies MT53100 [Spectrum-2, Flash recovery mode] [15b3:024e]
```

A healthy Spectrum-2 enumerates as `15b3:cf6c` and as an **Ethernet controller**. In recovery
it falls back to its hardware ID and appears as a **Memory controller**.

From mstflint `dev_mgt/tools_dev_types.c`, Spectrum-2 is defined with
`hw_dev_id = 0x24e`, `sw_dev_id = 53100` (= `0xcf6c`). `dm_is_livefish_mode()` returns true
when the PCI device ID equals the hardware ID — which is exactly the `024e` case.

```bash
sudo mst start && sudo mst status -v
Spectrum2(rev:0)  /dev/mst/mt590_pciconf0  07:00.0 ... recovery
```

`0x24e` = 590 decimal, so the node name is consistent with the same mechanism.

> **Note:** `dm_is_livefish_mode()` also recognises a "zombiefish" state that does **not**
> change the PCI ID. So a device showing `cf6c` can still have firmware problems — judge by
> whether `flint q` works, not by the PCI ID alone.

## Step 2 — Rule out the "missing firmware" theory

The original report of this fault assumed a community-built SONiC image lacked NVIDIA's
closed-source firmware. Checking:

```bash
ls -lL /etc/mlnx/fw-SPC2.mfa
-rw-r--r-- 1 root root 1701608 ... /etc/mlnx/fw-SPC2.mfa
```

Present, and the same size as the public copy on NVIDIA's GitHub. In sonic-buildimage,
`files/build_templates/sonic_debian_extension.j2` maps `$MLNX_SPC2_FW_FILE` to
`/etc/mlnx/fw-SPC2.mfa` and enables `mlnx-fw-manager.service`; `platform/mellanox/fw.mk` pins
the firmware version per branch. **Official SONiC Mellanox builds, master included, ship the
firmware.** The premise was wrong.

## Step 3 — What the tools report

```bash
sudo flint -d 07:00.0 q
-E- Failed to query Device: 07:00.0.
No valid FS4 image found. Check the flash parameters, if specified.

sudo mlxfwmanager --query -d 07:00.0
  Device Type:  Spectrum2
  PSID:                       <- empty
  Base MAC:     N/A
  Status:       Failed to open device
```

Empty PSID and `N/A` MAC are what make this look like a factory-blank ASIC. **They are not
evidence that the data is gone** — only that the tools stopped before reading it.

## Step 4 — Is the flash chip alive? (the turning point)

```bash
sudo flint -d 07:00.0 -ocr hw query
  Type                    MX25Lxxx
  TotalSize               0x2000000          <- 32 MB
  Banks                   0x1
  SectorSize              0x1000
  WriteBlockSize          0x10
  QuadEn                  1
  Flash0.WriteProtected   Top,1-Sectors
  JEDEC_ID                0x1920c2           <- Macronix, 256 Mbit
  TBS, BP[3:0]            0, 0001
```

Full, correct flash parameters. **Communication with the flash works.** The chip is fine;
only its contents are wrong.

Two things to carry forward:
- `TotalSize 0x2000000` — every address below derives from this.
- `WriteProtected Top,1-Sectors` — protection is at the **top**, so offset `0` is writable
  with no unlocking. Writing the device-data region, however, would need protection lifted.

## Step 5 — Recover the switch identity from the EEPROM

The system EEPROM is a separate i2c part, untouched by ASIC flash corruption:

```bash
sudo decode-syseeprom
Product Name     0x21  MSN3420
Part Number      0x22  MSN3420-CB2FC
Serial Number    0x23  MT21XXXXXXXX
Base MAC Address 0x24  1C:34:DA:XX:XX:XX
Manufacture Date 0x25  06/30/2021
MAC Addresses    0x2A  254
Platform Name    0x28  x86_64-mlnx_msn3420-r0
ONIE Version     0x29  2019.11-5.2.0020-115200
```

This alone solves the "no serial sticker, so the NVIDIA portal is unusable" problem. From
ONIE the equivalent is `onie-syseeprom`.

A vendor-extension TLV also carries the base GUID. Cross-check it against NVIDIA's documented
derivation, `guid2mac(g) = ((g >> 16) & 0xffffff000000) | (g & 0xffffff)` — the GUID is the
MAC with two bytes inserted mid-value. On this unit the derived MAC matched TLV `0x24`
exactly, confirming both values.

**Picking the PSID.** The part-number suffix encodes the shipped OS:

| Part number | Archive board name | PSID | SKU |
|---|---|---|---|
| `MSN3420-CB2F`  | `MSN3420-Cxxx_Ax`  | `MT_0000000301` | Onyx |
| `MSN3420-CB2FO` | `MSN3420-CxxxO_Ax` | `MT_0000000302` | ONIE |
| `MSN3420-CB2FC` | `MSN3420-CxxxC_Ax` | `MT_0000000303` | Cumulus |

This unit is `-CB2FC` → `MT_0000000303`. An earlier pass at this analysis picked `302` by
assuming "ONIE boot loader" from the running OS; the part number is what settles it.

## Step 6 — Is the device data still on the flash?

FS4 places its device-data table at `flash_size − FS4_DEFAULT_SECTOR_SIZE`:

- `fs4_ops.cpp:1270` — `u_int32_t dtoc_addr = _ioAccess->get_size() - FS4_DEFAULT_SECTOR_SIZE;`
- `fs4_ops.h:240` — `#define FS4_DEFAULT_SECTOR_SIZE 0x1000`

On 32 MB: `0x2000000 − 0x1000` = **`0x1FFF000`**.

```bash
sudo flint -d 07:00.0 -ocr rb 0x1fff000 0x100
0x44544f43 0x04081516 0x2342cafa 0xbacafe00 0x00000001 ...
```

`44 54 4F 43` is `DTOC`, and the bytes after it match the header mstflint writes
(`fs4_ops.cpp:1908`: `{0x44,0x54,0x4f,0x43,0x04,0x08,0x15,0x16,0x23,0x42,0xca,0xfa,0xba,0xca,0xfe,0x00,…}`).

Decoding the 32-byte entries — first byte is the section type, dword 5 is the flash address —
against `fs3_section_t` in `flint_base.h`:

| Type | Section | Address |
|---|---|---|
| 0xe5 | FS3_FW_NV_LOG | 0x01f00000 |
| 0xe6 | FS3_NV_DATA0 | 0x01f20000 |
| 0xe4 | FS3_NV_DATA2 | 0x01f40000 |
| 0xe1 | FS3_DEV_INFO | 0x01f60000 |
| 0xe1 | FS3_DEV_INFO | 0x01f70000 |
| **0xe0** | **FS3_MFG_INFO** | **0x01ff8000** |
| 0xe3 | FS3_VPD_R0 | 0x01ff8140 |

**All device data present**, confined to the top 1 MB. This is the finding that moves the
case out of "NVIDIA only" territory: `MFG_INFO` holds the PSID, GUID and MAC, so none of it
needs recreating with the OEM-only `smg` command.

Later confirmed directly by dumping `MFG_INFO` from the backup:

```
PSID       MT_0000000303
Base GUID  1c34da03XXXXXXXX
Base MAC   1c34daXXXXXX
Board name Phoenix_MSN3420-CxxxC_lionfish_internal_stamping.prs
```

The board name independently corroborates the `-CxxxC` (Cumulus) SKU deduced from the part
number — a third agreeing source.

## Step 7 — What exactly is broken at offset 0?

```bash
sudo flint -d 07:00.0 -ocr rb 0x0 0x40
0x00000000 0xabcdef00 0xfade1234 0x5678dead 0x01000100 0xffffffff
0x00000100 0x000075f8 0x00001000 0x0000eb67 0x00004000 0x0000bd3f
0x00000500 0x00008359 0x00000000 0x00000000
```

The FS4 magic is defined at `fw_ops.cpp:378`:

```c
const u_int32_t FwOperations::_fs4_magic_pattern[4] =
    {0x4D544657 /* "MTFW" */, 0xABCDEF00, 0xFADE1234, 0x5678DEAD};
```

The last three dwords match exactly. **Only the first is zeroed.**

That is the signature of `Fs3Operations::invalidateOldFWImages()` (`fs3_ops.cpp:4484`):

```c
u_int32_t zeroes = 0;
...
flash_access->write(image_start_addrs[i], &zeroes, sizeof(zeroes), true)
```

It writes exactly four zero bytes over the magic of an image being retired. So this flash was
not wiped or factory-blank — **a firmware burn ran here and did not finish.**

The bytes after the magic are healthy `{pointer, CRC}` pairs, not erased `0xFF`. With
`FS4_HW_PTR_START = 0x18` (`flint_base.h:283`) and the field order of
`struct image_layout_hw_pointers_carmel` (`tools_layouts/image_layout_layouts.h:594` —
`boot_record_ptr` +0x0, `boot2_ptr` +0x8, `toc_ptr` +0x10, `tools_ptr` +0x18):

```
0x18  boot_record_ptr = 0x100
0x20  boot2_ptr       = 0x1000
0x28  toc_ptr         = 0x4000
0x30  tools_ptr       = 0x500
```

**No second image to fall back on.** `FindAllImageStart` (`fw_ops.cpp:409`) scans 11 fixed
offsets (`fw_ops.cpp:380`):

```
0, 0x10000, 0x20000, 0x40000, 0x80000, 0x100000,
0x200000, 0x400000, 0x800000, 0x1000000, 0x2000000
```

`flint q` reporting "No valid FS4 image found" means that whole scan came up empty.

## Step 8 — Is the firmware body intact?

```bash
sudo flint -d 07:00.0 -ocr rb 0x4000 0x40
0x49544f43 0x04081516 ...          <- "ITOC"  (ITOC_ASCII 0x49544f43, fs3_ops.h:221)

sudo flint -d 07:00.0 -ocr rb 0x1000 0x40
0x10000040 0x00000bb0 0x0000b3db 0x940002f3 ...    <- real boot code, not 0xFF
```

The ITOC header sits exactly where `toc_ptr` says it should, independently confirming the
pointer decoding. This was enough to justify attempting the 4-byte repair.

## Step 9 — Repair the signature, then re-assess

After restoring the magic (see [README §3](../README.md#3-repair-the-4-byte-signature)),
`flint` stopped reporting a missing image and advanced to a different check:

```bash
sudo flint -d 07:00.0 -ocr v
  /0x00000018-0x00000097/ (HW_POINTERS) - OK      (all 16 entries)
  /0x00000500-0x0000053f/ (TOOLS_AREA)  - OK
  /0x00001000/ (BOOT2) /0x00001004/ - unexpected size (0xffffffff)
-E- FW image verification failed: Boot2 invalid size
```

`CheckBoot2` (`fw_ops.cpp:277-292`) reads a size dword at `boot2_ptr + 4` and rejects values
outside `4 … 1048576`.

A raw read at `0x1004` returns `0x00000bb0` = 2992, which is *inside* that range — yet flint
reads `0xffffffff` there. The raw dword is stale leftover; flint resolves the address through
its address convertor on a failsafe image and lands elsewhere. Either way the conclusion is
settled by direct evidence:

```bash
sudo flint -d 07:00.0 -ocr rb 0x3e00 0x100
0xffffffff 0xffffffff ... (all FF)
```

boot2 runs from `0x1000` to about `0x3ED0`, and `0x3e00` is erased. **The firmware body is
genuinely damaged**, so a signature patch alone could never have been sufficient. Everything
else — HW pointers, TOOLS_AREA, ITOC, DTOC, MFG_INFO — still verified.

## Step 10 — The `.mfa` archive format

Not documented by NVIDIA; determined by inspection:

```
offset 0x000  "MFAR" magic + header (last dword = length of stream 1)
offset 0x018  xz stream 1 - index: 44 records, fixed 0xe4 stride,
                            each = PSID + board name + description
offset 0x618  xz stream 2 - small metadata block
offset 0x6c0  xz stream 3 - payload: 44 consecutive FULL 32 MB flash images,
                            in the same order as the index
```

Confirmed by decompressing the payload and finding the FS4 magic at every `0x2000000`
boundary, with total size exactly 44 × 32 MB.

It is emphatically **not** a self-extracting executable — a widely-circulated answer suggests
running `fw-SPC2.mfa --sfx-extract-only`, which cannot work.

Validation of the extracted slot for `MT_0000000303` (index 7):

| Check | Result |
|---|---|
| Size | exactly `0x2000000` |
| Magic at 0 | `4d544657abcdef00fade12345678dead` ✓ |
| `MT_0000000303` present | at `0x3f5b44` and `0x1ff8000` ✓ |
| `MT_0000000302` / `MT_0000000301` present | **no** — slot is unambiguous ✓ |
| Last non-`FF` below `0x1f00000` | `0x4079bf` — firmware is only ~4 MB |
| Device-data region in the image | a **template**: carries the PSID, not any unit's MAC |

That last row is why the write must stop at `0x410000`: the archive image's device-data area
would overwrite a real switch's identity with a blank template.

## Step 11 — Result

After writing `0x0`–`0x410000` in 64 KB chunks:

```
-I- FW image verification succeeded. Image is bootable.

FW Version:  29.2020.1148
Base GUID:   1c34da03XXXXXXXX   254
Base MAC:    1c34daXXXXXX       254
PSID:        MT_0000000303
```

GUID and MAC identical to the EEPROM values read before any write. After a full power cycle:

```
07:00.0 Ethernet controller [0200]: Mellanox Technologies MT53100 [Spectrum-2] [15b3:cf6c] (rev a0)
```

`show interface status` listed the complete front panel — 48 × 25G (`etp1`–`etp48`) and
12 × 100G (`etp49`–`etp60`).

---

## Source index

Everything structural above traces to one of these, in
[Mellanox/mstflint](https://github.com/Mellanox/mstflint):

| Claim | Source |
|---|---|
| FS4 magic constant | `mlxfwops/lib/fw_ops.cpp:378` |
| The 11 image start offsets | `mlxfwops/lib/fw_ops.cpp:380` |
| `FindAllImageStart` / `FindMagicPattern` | `mlxfwops/lib/fw_ops.cpp:382-440` |
| boot2 size check → "Boot2 invalid size" | `mlxfwops/lib/fw_ops.cpp:277-292` |
| 4-byte signature invalidation | `mlxfwops/lib/fs3_ops.cpp:4484` |
| DTOC at `flash_size − 0x1000` | `mlxfwops/lib/fs4_ops.cpp:1270`, `fs4_ops.h:240` |
| DTOC header bytes | `mlxfwops/lib/fs4_ops.cpp:1908` |
| `FS4_HW_PTR_START 0x18` | `mlxfwops/lib/flint_base.h:283` |
| Section types (`MFG_INFO 0xe0` …) | `mlxfwops/lib/flint_base.h`, `fs3_section_t` |
| `ITOC_ASCII 0x49544f43` | `mlxfwops/lib/fs3_ops.h:221` |
| HW pointer field order | `tools_layouts/image_layout_layouts.h:594` |
| Spectrum-2 `hw_dev_id`/`sw_dev_id`, livefish test | `dev_mgt/tools_dev_types.c` |

SONiC side, in [sonic-net/sonic-buildimage](https://github.com/sonic-net/sonic-buildimage):
`platform/mellanox/fw.mk`, `platform/mellanox/sdk.mk`,
`files/build_templates/sonic_debian_extension.j2`,
`platform/mellanox/platform-utils/mellanox_fw_manager/`.

NVIDIA documentation (not redistributed here — download from
[networking-docs.nvidia.com](https://networking-docs.nvidia.com/)): MFT Documentation v4.37.0
(§3.9.3 livefish, §3.9.4.3 burning a new Spectrum device, low-level flash commands), the
Spectrum-2 SN3000 Hardware User Manual, and the ONIE User Manual.
