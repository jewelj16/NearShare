# NearShare

**NearShare** is a fast, peer-to-peer file transfer CLI tool for Linux. It allows you to seamlessly share files directly over the local network or automatically via a dynamically created Wi-Fi hotspot, without relying on cloud servers or the internet.

## Current Status
An interactive CLI has been fully implemented in Python, supporting automatic Wi-Fi hotspot discovery, handshake protocols, and progress tracking.

### Key Features Completed:
- **Interactive Wizard UI**: Simple `init` (send) and `receive` modes with an ASCII interface and dynamic progress bars.
- **Auto-Discovery**: The Sender automatically creates a dynamic WPA2 Wi-Fi Hotspot (`NearShare-<name>`) using `nmcli`.
- **Peer-to-Peer Engine**: Built on Python `asyncio` for high performance, handling chunked streaming, file metadata, and dynamic accept/reject prompts.
- **Cross-network transfers**: Fallback direct connection mode (`send` command) for devices already on the same LAN.

## Usage Commands

### 1. Auto-Discovery Mode (Wi-Fi Hotspot)
In this mode, the sender creates a temporary Wi-Fi hotspot (`NearShare-<name>`) using `nmcli`, and the receiver automatically scans for it, connects, and downloads the files.

* **Sender (Create hotspot and send files):**
  ```bash
  sudo python -m desktop.main init <file1> [<file2> ...]
  ```

* **Receiver (Auto-discover and receive files):**
  ```bash
  sudo python -m desktop.main receive [--save-dir <path>] [--auto-accept]
  ```

---

### 2. Direct Mode (Same Wi-Fi / LAN)
If both devices are already on the same Wi-Fi network, you can bypass hotspot creation and transfer files directly via IP address.

* **Receiver (Start listener):**
  ```bash
  python -m desktop.main receive --listen [--save-dir <path>] [--auto-accept]
  ```

* **Sender (Send to receiver IP):**
  ```bash
  python -m desktop.main send <file1> [<file2> ...] --host <RECEIVER_IP>
  ```

---

## Architecture and Flow

The NearShare engine is designed around an asynchronous state machine:
1. **Network Discovery**: 
   - **Sender**: Creates a Wi-Fi hotspot (`NearShare-xxxx`) with a randomly generated 8-digit password.
   - **Receiver**: Scans available Wi-Fi networks, displays them, and connects to the selected sender's hotspot.
2. **Handshake (`HELLO`)**: Once connected via TCP, devices exchange their `DeviceId` and `DisplayName`.
3. **Metadata Phase (`METADATA`)**: The sender shares file names and sizes. The receiver receives an accept/reject prompt.
4. **Transfer (`CHUNK`)**: Files are transferred asynchronously in chunks. The receiver acknowledges chunks (`ACK`) and verifies data integrity using SHA-256 hashes.
5. **Completion (`TRANSFER_COMPLETE`)**: Files are successfully written to the local disk, safely resolving any naming conflicts.

## Future Enhancements
1. Improving Speed of File Transfer
2. Improve UI
3. Group Share Feature
4. GUI App
5. Create Android app and allow transfer between phone and computer
