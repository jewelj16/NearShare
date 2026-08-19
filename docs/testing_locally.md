# How to Test NearShare Locally (Same Laptop, Two Processes)

This guide explains how to test NearShare by running two separate processes (a sender and a receiver) on the same laptop.

## Quick Test (Direct Mode)

Since both processes are on the same machine, we use `--listen` on the receiver and `--host 127.0.0.1` on the sender.

### Step 1: Create a Test File

```bash
head -c 10485760 /dev/urandom > test_payload.bin
```

### Step 2: Start the Receiver (Terminal 1)

```bash
python -m desktop.main receive --listen --save-dir /tmp/nearshare_received --auto-accept
```

### Step 3: Start the Sender (Terminal 2)

```bash
python -m desktop.main send test_payload.bin --host 127.0.0.1
```

### Step 4: Verify

```bash
sha256sum test_payload.bin
sha256sum /tmp/nearshare_received/test_payload.bin
```

Both hashes should match.

---

## Testing Auto-Discovery Mode (Hotspot)

To test the full hotspot-based auto-discovery flow on a single machine, you need a Wi-Fi interface that supports AP mode. This won't work if you only have ethernet.

### Terminal 1 (Sender)

```bash
sudo python -m desktop.main init test_payload.bin
```

### Terminal 2 (Receiver)

```bash
sudo python -m desktop.main receive --auto-accept
```

The receiver will scan for the `NearShare-*` hotspot, connect, and the transfer will proceed automatically.

> **Note:** On a single machine this may cause network disruption since the hotspot replaces your current Wi-Fi connection. For reliable local testing, use direct mode above.

---

## Troubleshooting

* **Address already in use:** Another process is holding port 47321. Kill it or wait.
* **ModuleNotFoundError:** Run from the project root as `python -m desktop.main ...`.
