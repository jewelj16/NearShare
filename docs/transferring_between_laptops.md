# How to Transfer Files Between Two Laptops

NearShare supports two modes for laptop-to-laptop transfer:

1. **Auto-discovery mode** (recommended) — the sender creates a Wi-Fi hotspot, the receiver scans and connects automatically. No IP addresses needed.
2. **Direct mode** — you manually specify the receiver's IP address. Use when both laptops are already on the same network.

---

## Mode 1: Auto-Discovery (Hotspot Mode)

This is the easiest way. The sender creates a temporary Wi-Fi hotspot and the receiver finds it automatically. No IP addresses, no port numbers.

> **Note:** This mode requires `sudo` for hotspot creation/teardown.

### On the Sender (Laptop A)

```bash
sudo python -m desktop.main init photo.jpg document.pdf
```

This will:
1. Save your current Wi-Fi connection.
2. Create a temporary hotspot (e.g. `NearShare-7f3a`).
3. Start listening for the receiver.
4. After the transfer, tear down the hotspot and reconnect to your original Wi-Fi.

You'll see output like:
```
Creating NearShare hotspot...
  SSID:     NearShare-7f3a
  Password: NearShare2026
  Gateway:  10.42.0.1

Listening on 10.42.0.1:47321
Waiting for receiver to connect...
```

### On the Receiver (Laptop B)

```bash
sudo python -m desktop.main receive --auto-accept
```

This will:
1. Save your current Wi-Fi connection.
2. Scan for nearby `NearShare-*` hotspots.
3. Automatically connect to the strongest one.
4. Connect to the sender and receive the files.
5. After the transfer, disconnect from the hotspot and reconnect to your original Wi-Fi.

Files are saved to `~/Downloads/NearShare/` by default. Use `--save-dir` to change:
```bash
sudo python -m desktop.main receive --save-dir /path/to/folder
```

### What If No Hotspot Is Found?

The receiver scans for 30 seconds by default. You can increase this:
```bash
sudo python -m desktop.main receive --scan-timeout 60
```

Make sure the sender has run `nearshare init` before the receiver starts scanning.

---

## Mode 2: Direct Mode (Same Network)

Use this when both laptops are already on the same Wi-Fi network and you know the receiver's IP address.

### On the Receiver (Laptop A)

```bash
python -m desktop.main receive --listen --auto-accept
```

The `--listen` flag tells the receiver to wait for direct connections instead of scanning for hotspots.

### Find the Receiver's IP Address

On the receiving laptop:
```bash
ip addr show | grep -w inet
```
Look for an address like `192.168.1.5` (ignore `127.0.0.1`).

### On the Sender (Laptop B)

```bash
python -m desktop.main send photo.jpg --host 192.168.1.5
```

---

## CLI Reference

```
nearshare init <files...>                        # Auto: create hotspot + send
nearshare send <files...> --host <ip>            # Direct: send to specific IP
nearshare receive                                # Auto: scan for hotspots + receive
nearshare receive --listen                       # Direct: wait for incoming connections
```

### Common Flags

| Flag | Command | Description |
|------|---------|-------------|
| `--port PORT` | all | TCP port (default 47321) |
| `--name NAME` | all | Display name for this device |
| `--auto-accept` | receive | Skip the accept/reject prompt |
| `--listen` | receive | Wait for direct connections (skip hotspot scan) |
| `--save-dir DIR` | receive | Where to save received files |
| `--scan-timeout SECS` | receive | How long to scan for hotspots (default 30s) |
| `--verbose` / `-v` | all | Enable debug logging |

---

## Troubleshooting

* **"No Wi-Fi interface found"** — Your wireless adapter may be disabled or not detected. Check with `nmcli device status`.
* **"Failed to create hotspot"** — Not all Wi-Fi chipsets support AP (hotspot) mode. Check with `iw list | grep -A5 "Supported interface modes"` and look for `AP`.
* **"No NearShare hotspots found"** — Make sure the sender ran `init` first. Also check that the receiver's Wi-Fi radio is on: `nmcli radio wifi on`.
* **"Connection refused"** — Check that port 47321 is not blocked by a firewall: `sudo ufw allow 47321/tcp`.
* **Original Wi-Fi not restored** — If NearShare crashes, run `nmcli connection up "YourWifiName"` to reconnect manually.
