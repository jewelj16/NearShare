# Interactive UI Flow — Feasibility Study

## Proposed User Flow

The user proposes shifting from a strict "one-shot" command-line interface (where all arguments like files and IPs must be provided upfront) to an **Interactive Wizard** flow.

### Sender:
1. Starts empty: `sudo python -m desktop.main init`
2. Prompts for a short temporary name (max 6 chars) to use for the hotspot.
3. Generates a random secure password and displays it.
4. Waits for connection.
5. Upon connection, displays the receiver's name.
6. Prompts for file paths (in a validation loop).
7. Prompts to confirm sending.
8. Displays 0-100% progress.
9. Returns to main menu (or exits).

### Receiver:
1. Starts empty: `sudo python -m desktop.main receive`
2. Prompts for a username (display name).
3. Scans for `NearShare-*` hotspots.
4. Displays a numbered list of available senders.
5. User selects a sender.
6. User enters the random password given by the sender.
7. Connects and shows receive progress (0-100%).
8. Returns to main menu (or exits).

---

## Technical Feasibility Analysis

**Verdict:** **Highly Feasible.** This flow is much more user-friendly and closely mimics the experience of modern sharing apps, adapted for a terminal. 

Here is how the technical challenges will be solved:

### 1. Dynamic Hotspot Naming & Random Password
* **Feasibility:** Trivial.
* **Implementation:** Instead of hardcoding `HOTSPOT_PASSWORD = "NearShare2026"`, we generate a random 6-8 character alphanumeric string using Python's `secrets` module. The SSID becomes `NearShare-<UserName>`.

### 2. Knowing the Receiver's Name Before Sending Files
* **Challenge:** When a device connects to the Wi-Fi hotspot, the Wi-Fi layer (NetworkManager) only gives us a MAC address, not a human-readable name. 
* **Solution:** We don't ask for files when the *Wi-Fi* connects. We wait for the receiver to initiate the *TCP connection* and send the NearShare `HELLO` protocol message. The `HELLO` message contains the receiver's display name. 
* **Engine Change:** We will need to slightly refactor `TransferSender.run()`. Currently, it requires the list of files upfront. We will decouple the `HELLO` handshake from the file-sending loop, allowing the CLI to intercept the connection, read the peer's name, prompt the user for files, and *then* proceed with the metadata phase.

### 3. File Input Validation Loop
* **Feasibility:** Trivial.
* **Implementation:** A simple `while True:` loop in `init_cmd.py` using Python's `input()` that uses `Path.exists()` and `Path.is_file()` to validate input, repeating until valid.

### 4. Progress Bars (0 to 100%)
* **Feasibility:** Highly Feasible.
* **Implementation:** Currently, the engine uses `logger.info()` which scrolls the terminal. We will suppress debug logging in interactive mode and introduce a progress callback (or use a library like `tqdm` if we want external dependencies, but a simple carriage-return `\r` print statement works perfectly with standard library). The `TransferSender` and `TransferReceiver` orchestrators already know the total bytes and track ACKed bytes, so calculating percentages is straightforward.

### 5. Main Menu Loop
* **Feasibility:** Trivial.
* **Implementation:** We can wrap `desktop.main.main()` in an interactive loop (e.g., asking "Do you want to send or receive? (s/r/q)") if the user runs `nearshare` with no arguments, creating a persistent application rather than a single-fire command.

---

## Execution Prompt

*(If you are ready to implement this, copy and paste the block below as your next request)*

```text
Please implement the interactive CLI flow defined in docs/user-int.md. 

Key requirements:
1. Update `desktop/network/hotspot.py` to accept dynamic SSIDs and passwords, generating a random 6-8 char password if none is provided.
2. Modify the Engine (`TransferSender` and `TransferReceiver`) to support progress callbacks (`on_progress(bytes_done, total_bytes)`).
3. Refactor the `HELLO` handshake flow so the sender can discover the receiver's name *before* needing the list of files.
4. Rewrite `desktop/commands/init_cmd.py` and `receive_cmd.py` to use Python `input()` for the interactive wizard (username, hotspot selection, password entry, file entry loop).
5. Add a `\r`-based progress bar to the CLI output that uses a flight-like animation (e.g. `[======>    ] 60%`) and displays a live transfer speed update (e.g., `8 MiB/s`).
6. Ensure all unit tests are updated to reflect the new engine signatures.
7. Always display the following ASCII logo when the CLI starts:
 _   _                 _____ _
| \ | |               / ____| |
|  \| | ___  __ _ _ _| (___ | |__   __ _ _ __ ___
| . ` |/ _ \/ _` | '__\___ \| '_ \ / _` | '__/ _ \
| |\  |  __/ (_| | |  ____) | | | | (_| | | |  __/
|_| \_|\___|\__,_|_| |_____/|_| |_|\__,_|_|  \___|
8. Underneath the ASCII logo, print the current mode ("SEND" or "RECEIVE") in a smaller, standard font.
9. Apply ANSI color codes to the ASCII logo so that the "Near" portion is printed in orange and the "Share" portion is printed in white.
```

---

## UDP Flooding & Discovery Feasibility

**The Problem:** UDP/IP requires both devices to already be on the same network. You cannot broadcast UDP packets across raw Wi-Fi without first associating to a shared access point.

**Feasible Alternatives to "Flood" packets without an IP Network:**
1. **Wi-Fi Beacon Frames (Current Approach):** By creating a hotspot with an SSID prefix (e.g., `NearShare-`), we are essentially using raw 802.11 Beacon management frames to "flood" our presence. Any device within range can detect these beacons using a passive/active Wi-Fi scan without being connected. This is the most reliable, cross-platform approach for unrooted devices.
2. **Bluetooth Low Energy (BLE) Advertising:** BLE beacons can be broadcasted freely without pairing or connection. We can advertise a payload containing the Wi-Fi hotspot's SSID and a cryptographic payload. This approach is highly robust and is what AirDrop and Google Nearby Share use to bootstrap the Wi-Fi Direct connection.
3. **Apple Wireless Direct Link (AWDL) / Wi-Fi Aware (NAN):** Newer standards like Wi-Fi Aware (Neighbor Awareness Networking) allow devices to discover each other and exchange small strings of data without a conventional access point. Support across older Linux network managers is spotty, but it is the industry standard for this exact problem.
4. **802.11 Probe Requests / Custom IEs:** We could theoretically inject custom Information Elements (IEs) into Wi-Fi Probe Requests. However, this typically requires root privileges and a wireless interface capable of monitor/packet-injection mode, making it unfeasible for a seamless consumer app.

**Conclusion:** Our current approach of using the Hotspot SSID itself as the discovery broadcast (via standard Wi-Fi Beacons) is the most feasible and elegant solution for Linux without requiring BLE hardware or monitor-mode Wi-Fi drivers.

### Next Steps Prompt

*(If you wish to explore BLE for discovery, use this prompt)*

```text
Please investigate adding a BLE-based discovery layer to NearShare. 
1. The sender should advertise a BLE beacon containing the hotspot SSID prefix.
2. The receiver should scan for BLE beacons and automatically retrieve the SSID.
3. Evaluate python libraries like `bleak` for cross-platform BLE support.
```
