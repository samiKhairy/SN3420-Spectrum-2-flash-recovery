# Backup and restore

Take these backups on a **healthy** switch and the same failure becomes a ten-minute job
instead of a multi-day investigation.

Replace `<pci>` with your address from `lspci -nn -d 15b3:` (e.g. `07:00.0`).

---

## What is actually irreplaceable

| Data | Replaceable? | Why |
|---|---|---|
| Firmware image | **Yes** | Byte-reproducible from NVIDIA's public `.mfa`, with a verifiable sha256 |
| **Device data** — `0x1F00000`–`0x1FFFFFF`: `MFG_INFO`, `DEV_INFO`, `VPD_R0`, `DTOC` | **No** | Holds this unit's unique GUID, MAC and PSID stamp |
| System EEPROM | Separate chip | Survives ASIC flash corruption; holds serial and base MAC |

**The backup that matters is the 1 MB device-data region.** Everything else can be rebuilt
from public sources plus this repository.

If device data is lost with no backup, recovery needs `smg` ("set manufacture GUIDs"), which
requires the OEM-only build of `flint`. That is a genuine NVIDIA support case.

---

## Taking the backups

Store them on persistent storage. On SONiC use `/host`, **not** `/tmp` — `/tmp` is cleared on
reboot, which destroyed the first set of backups taken during the original recovery.

> **Drop `-ocr` on a healthy switch.** The warning *"Firmware flash cache access is enabled…
> may cause the firmware to hang"* is harmless when no firmware is running, but a real risk
> once it is. `-ocr` is for a device in recovery mode only.

```bash
# 1. Device data - THE critical one (1 MB)
sudo flint -d <pci> rb 0x1f00000 0x100000 /host/backup-devdata.bin

# 2. Golden firmware image, read back from the working chip
sudo flint -d <pci> ri /host/backup-golden.bin

# 3. Boot sector, for the cheap signature-only repair
sudo flint -d <pci> rb 0x0 0x1000 /host/backup-sector0.bin

# 4. Inventory and state, as text
sudo decode-syseeprom       > /host/backup-syseeprom.txt
sudo flint -d <pci> q       > /host/backup-flint-query.txt
sudo flint -d <pci> hw query > /host/backup-flash-hw.txt

sha256sum /host/backup-*    > /host/backup-SHA256SUMS.txt
```

Verify the golden image is a valid firmware file, not merely bytes on disk:

```bash
sudo flint -i /host/backup-golden.bin v | tail -3
```

Confirm the device-data backup really captured `MFG_INFO` — `0x1FF8000` is at offset
`0xF8000` within it:

```bash
python3 -c "d=open('/host/backup-devdata.bin','rb').read(); print(len(d)); print(d[0xf8000:0xf8030])"
```

Expect `1048576` and your PSID as ASCII. **If the PSID is not there, the backup is not
usable — re-read the region before trusting it.**

Then copy everything off the switch (see [Getting files off](#getting-files-off-the-switch)).

---

## Restoring

### First, identify which case you are in

```bash
lspci -nn -d 15b3:                              # 024e = recovery, cf6c = normal
sudo mst start
sudo flint -d <pci> -ocr hw query               # proves the flash is reachable
sudo flint -d <pci> -ocr rb 0x0 0x10            # is the signature intact?
sudo flint -d <pci> -ocr rb 0x1fff000 0x20      # is "DTOC" (0x44544f43) still there?
```

| Observation | Case |
|---|---|
| `hw query` fails entirely | Hardware/access problem — none of this applies |
| First dword `0x00000000`, next three `abcdef00 fade1234 5678dead` | **A** |
| Signature fine, `flint -ocr v` fails past it | **B** |
| No `DTOC` at `0x1fff000`, or `MFG_INFO` blank | **C** |
| Case C and no backup | **D** |

### Case A — signature zeroed, firmware body intact

Minutes; writes 4 KB.

```bash
sudo flint -d <pci> -ocr rb 0x0 0x1000 /tmp/sector0.bin
python3 tools/patch_signature.py /tmp/sector0.bin /tmp/sector0-fixed.bin
sudo flint -d <pci> -ocr wb /tmp/sector0-fixed.bin 0x0
sudo flint -d <pci> -ocr v
```

If `v` succeeds, power-cycle (both cords, 30 s) and you are done. If it fails at `BOOT2` or
later, you are also in Case B.

### Case B — firmware body damaged

Write the firmware region, and only the firmware region:

```bash
# either re-extract from the archive on the box...
python3 tools/mk_fwpart.py --list /etc/mlnx/fw-SPC2.mfa
python3 tools/mk_fwpart.py /etc/mlnx/fw-SPC2.mfa <YOUR_PSID> /tmp/fwpart.bin

# ...or take the first 0x410000 of your golden image
dd if=/host/backup-golden.bin of=/tmp/fwpart.bin bs=65536 count=65

sudo systemctl stop syncd swss pmon
sudo mst start

for i in $(seq 0 64); do
  dd if=/tmp/fwpart.bin of=/tmp/chunk.bin bs=65536 skip=$i count=1 2>/dev/null
  sudo flint -d <pci> -ocr wb /tmp/chunk.bin $((i*65536)) || { echo "FAILED at chunk $i"; break; }
  echo "chunk $i/64 ok"
done

sudo flint -d <pci> -ocr v
sudo flint -d <pci> -ocr q     # must show YOUR original MAC before you reboot
```

**Never exceed `0x410000`.** Above that lies the device-data region.

### Case C — device data damaged, backup available

> **Untested.** This case never arose during the original recovery; the procedure is
> reconstructed from the MFT manual and the flash geometry rather than verified in practice.
> Treat it as a starting point and verify each step.

The top sector is write-protected (`Flash0.WriteProtected = Top,1-Sectors`), so protection
must be lifted first:

```bash
sudo flint -d <pci> -ocr hw query          # note the current WriteProtected value
sudo flint -d <pci> -ocr hw set Flash0.WriteProtected=Disabled

for i in $(seq 0 15); do
  dd if=/host/backup-devdata.bin of=/tmp/dchunk.bin bs=65536 skip=$i count=1 2>/dev/null
  sudo flint -d <pci> -ocr wb /tmp/dchunk.bin $((0x1f00000 + i*65536)) || { echo "FAILED at $i"; break; }
  echo "devdata chunk $i/15 ok"
done

sudo flint -d <pci> -ocr hw set Flash0.WriteProtected=Top,1-Sectors    # restore protection
sudo flint -d <pci> -ocr v
```

Restore the protection setting your own `hw query` reported, which may differ from
`Top,1-Sectors`.

### Case D — device data gone, no backup

MFT §3.9.4.3.1.2 applies: the GUIDs and MACs must be re-stamped with `smg`, which needs
"flint for OEM". That build is not public.

Open an NVIDIA support case with the serial number from `decode-syseeprom` — the EEPROM is a
separate chip and almost certainly still readable, which also solves the "no serial sticker"
problem. Include the `flint -ocr hw query` and `flint -ocr v` output.

**Taking the backups above is what keeps you out of this case.**

---

## Getting files off the switch

**Management port**, if reachable:

```bash
ip addr show eth0
# if unconfigured, cable it to a laptop:
sudo ip addr add 192.168.100.2/24 dev eth0 && sudo ip link set eth0 up
```
then from the workstation: `scp admin@192.168.100.2:/host/backup-* .`

**USB stick**, if you only have serial access:

```bash
lsblk
sudo mkdir -p /mnt/usb && sudo mount /dev/sdb1 /mnt/usb
sudo cp /host/backup-* /mnt/usb/ && sync && sudo umount /mnt/usb
```

Confirm the device name from `lsblk` rather than assuming `sdb1`.

**Over the serial console**, last resort: enable session logging in your terminal, run
`base64 /host/backup-devdata.bin`, decode on the workstation, and **verify the sha256** — one
dropped character corrupts the file, and at 115200 baud a 1 MB file takes several minutes.

---

## A note on sharing backups

A golden image and a device-data backup both contain `MFG_INFO`, which carries **your
switch's GUID and MAC**. They identify your specific hardware, and they are wrong for anyone
else's switch.

Share the *method*, not your dumps. The firmware itself is already public — anyone can
extract their own from NVIDIA's archive with `tools/mk_fwpart.py`.
