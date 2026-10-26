# Installing SONiC on a bare switch from ONIE

How to identify your platform, get the right image, and install it from a laptop acting as
the server — no DHCP infrastructure, no PXE environment, no internet on the switch.

This comes **before** the recovery work in this repository: the ASIC diagnosis and repair
need `flint` / `mlxfwmanager`, which ship inside the SONiC image. Get the OS on first, then
deal with the ASIC.

---

## 1. Find your platform and which image you need

The SONiC project maintains a table of every supported switch:

**<https://github.com/sonic-net/SONiC/blob/sonic_image_md_update/supported_devices_platforms.md>**

Columns are **Vendor | Platform | ASIC Vendor | Switch ASIC | Port Configuration | Image**.
Find your model and read across. For example:

| S.No | Vendor | Platform | ASIC Vendor | Switch ASIC | Port Configuration | Image |
|---|---|---|---|---|---|---|
| 115 | Nvidia | **SN3420** | Nvidia | **Spectrum 2** | 48x25G + 12x100G | SONiC-ONIE-Mellanox |

Three things to take from that row:

- **Which image family.** `SONiC-ONIE-Mellanox` → the file is `sonic-mellanox.bin`. Broadcom
  boxes take `sonic-broadcom.bin`, Arista uses `sonic-aboot-broadcom.swi`, and so on. The
  image is per **ASIC vendor**, not per model — one `sonic-mellanox.bin` covers every Nvidia
  platform in the table.
- **Your ASIC generation** (Spectrum / Spectrum-2 / Spectrum-3 …). You need this later to pick
  the right firmware archive — `fw-SPC2.mfa` for Spectrum-2.
- **Confirmation the platform is supported at all.** If your model is absent, a generic image
  will boot but will have no platform definition, and ports will not come up.

Neighbouring Nvidia rows, for reference: SN2010/2100/2201/2410/2700 are Spectrum,
SN3420/3700/3700C/3800 are **Spectrum-2**, SN4410/4600C/4600V/4700 are Spectrum-3, SN5600 is
Spectrum-4.

### Getting the image

The links in that table point at Azure build artifacts and **expire**. A stable URL that
always resolves to the latest successful build:

```
https://sonic-build.azurewebsites.net/api/sonic/artifacts?branchName=202511&platform=mellanox&target=target/sonic-mellanox.bin
```

Swap `202511` for the branch you want (`202311`, `202411`, `202505`, `master`), and
`mellanox` / `sonic-mellanox.bin` for your ASIC vendor's image. Browse builds at
<https://sonic-build.azurewebsites.net/ui/sonic/pipelines>.

Do not send a `Range:` header — the redirect target rejects it.

> The table at `sonic-net.github.io/SONiC/sonic_latest_images.html` shows **N/A** for Mellanox
> on most branches. That table reads a stale JSON that only tracks up to 202305. It does
> **not** mean no builds exist; the API URL above works.

The downloaded `.bin` is a self-extracting ONIE installer with an embedded SHA-1 that it
verifies on install. You can see it with `head -c 2000 sonic-mellanox.bin | grep payload_sha1`.

---

## 2. Set your laptop up as the server

The switch needs two things from you: **an IP address** (DHCP) and **a file to download**
(HTTP). On Windows, the tidiest split is tftpd64 for DHCP and Python for HTTP.

### Wiring

Connect your laptop's Ethernet port to the switch's **management port** (`MGMT` / `eth0`) —
**not** a front-panel data port. A direct cable is fine.

Give your laptop a static address on that adapter:

```
IP address    192.168.0.1
Subnet mask   255.255.255.0
Gateway       (leave blank)
```

### DHCP — tftpd64

[tftpd64](https://pjo2.github.io/tftpd64/) (Ph. Jounin, free) bundles a TFTP server, **DHCP
server**, DNS, SNTP and syslog. We only need its DHCP server; it is far quicker than
installing a real DHCP service.

1. Run **tftpd64 as Administrator**.
2. *Settings → GLOBAL*: enable **DHCP Server** and **TFTP Server**. Apply and restart it.
3. On the **DHCP server** tab:
   - *Pool starting address*: `192.168.0.2`
   - *Size of pool*: `10`
   - *Mask*: `255.255.255.0`
   - *Def. router*: `192.168.0.1`
   - *Current Directory*: the folder holding your `.bin`
   - *Server interface*: the adapter you set to `192.168.0.1`
4. Click **Save**.

ONIE will now pick up `192.168.0.2`.

> tftpd64 has **no HTTP server** — that is what Python is for below. You *can* install over
> TFTP (`onie-nos-install tftp://192.168.0.1/sonic-mellanox.bin`), but TFTP is slow and
> fragile for a ~1 GB file. Use HTTP.

### HTTP — Python

From the folder containing the image:

```powershell
cd C:\path\to\images
python -m http.server 80
```

Port 80 may need an Administrator prompt. Any port works if you put it in the URL —
`python -m http.server 8080` → `http://192.168.0.1:8080/...`.

Check it from your own browser first: `http://192.168.0.1/sonic-mellanox.bin` should start
downloading.

### Firewall

**Windows Firewall blocks this by default and is the usual reason nothing happens.** Allow
Python and tftpd64 through on the **Private** profile, or temporarily disable the firewall on
that adapter only while you install.

---

## 3. Boot the switch into ONIE

Connect to the console: **115200 8N1**, using PuTTY, minicom or screen.

At the GRUB menu during boot, choose **ONIE**, then:

- **ONIE: Install OS** — normal install
- **ONIE: Rescue** — a shell with no auto-discovery; handy if discovery is fighting you
- **ONIE: Uninstall OS** — wipes an existing NOS first, if you are replacing one

In install mode ONIE starts hunting for an installer on its own — DHCP options, TFTP, a USB
stick, then a long list of guessed filenames:

```
Info: Attempting file://dev/sda3/onie-installer-x86_64-mlnx_msn3420-r0 ...
Info: Attempting tftp://onie-server/onie-installer-x86_64-mlnx_msn3420-r0 ...
...
ONIE: Using DHCPv4 addr: eth0: 192.168.0.2 / 255.255.255.0
ONIE: Starting ONIE Service Discovery
```

Seeing `192.168.0.2` confirms your tftpd64 DHCP server is working.

**Stop the discovery loop before installing** — its retries will interleave with your install
and make the output unreadable:

```bash
onie-stop
```

---

## 4. Install

```bash
onie-nos-install http://192.168.0.1/sonic-mellanox.bin
```

Expected output:

```
discover: installer mode detected.
Stopping: discover... done.
Info: Attempting http://192.168.0.1/sonic-mellanox.bin ...
Connecting to 192.168.0.1 (192.168.0.1:80)
installer            85% |*************************   |  1112M  0:00:17 ETA
```

Then it verifies the embedded checksum, writes the image, and reboots into SONiC on its own.
Do not interrupt it.

The filename is irrelevant when you pass an explicit URL — `onie-mellanox.bin`,
`sonic-mellanox.bin`, anything. Names only matter for ONIE's *automatic* discovery, which
looks for specific patterns like `onie-installer-x86_64-mlnx_msn3420-r0`.

### First login

Default SONiC credentials: **`admin` / `YourPaSsWoRd`**. Change it immediately with `passwd`.

```bash
show version
show platform summary      # expect your platform, HwSKU and ASIC
show interface status
```

---

## 5. If ports do not appear

`show interface status` being empty at this point is exactly where
[the recovery guide](../README.md) starts. Check the ASIC first:

```bash
lspci -nn -d 15b3:
```

- **`15b3:cf6c`** (Ethernet controller) — ASIC is healthy; the problem is configuration. Check
  `show platform summary` matches your model, and look at `docker ps | grep syncd`.
- **`15b3:024e`** (Memory controller, "Flash recovery mode") — the ASIC firmware is broken.
  Go to the [recovery guide](../README.md).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| ONIE never gets an IP | Cable in a data port instead of MGMT; tftpd64 bound to the wrong adapter; DHCP server not enabled in *Settings → GLOBAL* |
| ONIE gets an IP, download fails instantly | Windows Firewall blocking Python; HTTP server started in the wrong directory |
| Download starts then stalls | TFTP instead of HTTP for a large image; flaky link — try a different cable or disable adapter power saving |
| `Unable to verify archive checksum` | Truncated download. Re-fetch and compare sizes; do not install a partial image |
| Installs, boots, but no ports | Platform unsupported in that image, or the ASIC issue above — check `lspci` |
| Discovery messages flood the console | Run `onie-stop` before `onie-nos-install` |

---

## Sources

- SONiC supported platforms: <https://github.com/sonic-net/SONiC/blob/sonic_image_md_update/supported_devices_platforms.md>
- SONiC build pipelines: <https://sonic-build.azurewebsites.net/ui/sonic/pipelines>
- tftpd64: <https://pjo2.github.io/tftpd64/>
- ONIE user guide: <https://opencomputeproject.github.io/onie/user-guide/>
- NVIDIA ONIE User Manual: <https://networking-docs.nvidia.com/onieswum>
