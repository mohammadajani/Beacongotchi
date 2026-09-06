# Wardrive MVP - RPi Zero 2W + e-paper + BW16

## What's in this folder
- `bw16_wardrive_mvp.ino` — flash this onto the BW16. Alternates WiFi
  network scans and BLE scans, prints results over USB-serial.
- `pi_eink_display_mvp.py` — runs on the Pi. Reads the BW16's serial
  output, logs to CSV, updates the e-paper display, and runs a small
  HTTP server for Termux GPS pushes.
- `dashboard.py` — run THIS instead of the script above. It's a Flask
  web dashboard (http://<pi-ip>:5000) that launches and manages
  pi_eink_display_mvp.py as a subprocess, shows live logs, lets you
  change display refresh behavior, upload a blank-screen graphic,
  upload a replacement capture script, and export the CSVs.
- `termux_gps_push.sh` — run this on your phone in Termux to push GPS
  location to the Pi over your hotspot connection.

## Hardware setup
- BW16 connects to the RPi Zero 2W via micro-USB-to-USB-A OTG cable
  (not GPIO/UART) since GPIO is used by the e-paper display.
- Display is a Waveshare 2.13" B (red/black/white), driven via
  `epd2in13b_V4` from Waveshare's `waveshare_epd` python package
  (cloned separately from https://github.com/waveshare/e-Paper —
  not pip-installable, must sit as a folder next to these scripts).
- Panel resolution: 250x122, 3 colors only (white/black/red), no
  partial refresh (hardware limitation of color e-paper).

## Pi-side Python environment
Everything runs from a venv at `./venv`. Required packages:
```
venv/bin/pip install flask pillow pyserial spidev RPi.GPIO gpiozero lgpio
```
`lgpio` matters specifically on newer Raspberry Pi OS (Trixie/Debian 13) -
without it, gpiozero falls back to a broken RPi.GPIO edge-detection path
and crashes with "Failed to add edge detection".

Always invoke scripts with the venv's own interpreter, especially under
sudo (sudo does not inherit an activated venv):
```
sudo venv/bin/python3 dashboard.py
```

## BW16 / Arduino IDE setup
- Board core: Realtek AmebaD (Ai-Thinker BW16 / RTL8720DN)
- Remove/uninstall the `ArduinoBLE` and `WiFiNINA` libraries if
  installed — they conflict with AmebaD's own WiFi/BLE libraries and
  get silently picked instead, causing `'BLE' was not declared` type
  errors.
- AmebaD's WiFi library is NOT the ESP32 API - no `.mode()`, no
  `.scanDelete()`, `BSSID()` is only for the currently-connected AP
  (not indexable per scan result). WiFi networks are deduped by SSID
  here as a result.
- AmebaD's BLE library is NOT ESP32/NimBLE-style - no
  `BLEAdvertisedDevice`. Uses a global `BLE` object from
  `BLEDevice.h`/`BLEScan.h`; the default (no custom callback) scan
  handler prints MAC+RSSI to Serial for us.
- Full-band `WiFi.scanNetworks()` can hang indefinitely on channel 165
  (known RTL8720 firmware quirk on some 5GHz DFS channels) - firmware
  restricts scanning to 2.4GHz only via the lower-level
  `wifi_set_pscan_chan()` SDK call to avoid this.
- If flashing over USB passthrough from a Kali VM (virt-manager), the
  BW16 may re-enumerate under a different USB ID when it drops into
  bootloader mode for upload - pass through by bus/port location, or
  add both IDs as separate USB Host Device entries.

## Data format / limitations (read before uploading anywhere like WiGLE)
- `wifi_log.csv`: timestamp, ssid, rssi, encryption, lat, lon
- `bt_log.csv`: timestamp, mac, rssi, lat, lon
- WiFi entries have NO BSSID (AmebaD's simple WiFi wrapper doesn't
  expose it per scan result) - only SSID-level dedup. Getting real
  BSSIDs would require rewriting the WiFi scan with the lower-level
  `wifi_scan_networks()` SDK call (same layer as the channel-lock fix).
- lat/lon are blank unless a GPS fix arrived within the last 15s via
  the Termux push script.
- This is a MANAGED-MODE scan (like a phone's WiFi picker), not
  monitor-mode/promiscuous packet capture - no client MACs, no
  handshakes, no per-frame data like airodump-ng gives you. True
  packet-level capture is being pursued separately in the
  wardriving-firmware / "Peek" project (ESP32-S3 + BW16, concurrent
  WiFi+BLE via CaptureRadio bitmask flags).
- Not WiGLE-upload-format-ready as-is (missing BSSID + real GPS
  integration end-to-end) - noted as a "next batch" item.

## Display behavior
Controlled via `settings.json` (written by the dashboard, read by the
capture script every scan cycle - so changes take one cycle, ~14s, to
apply):
- `refresh_mode`: "immediate" (repaint on any count/GPS-fix change),
  "interval" (repaint at most every N seconds), or "off" (freeze,
  show blank_content instead).
- `blank_content`: "white", "black", or "custom" (an uploaded image,
  auto-fit/padded to 250x122 and quantized to white/black/red).
Capture (scanning + CSV logging) always keeps running regardless of
display mode - only what's shown on the panel is affected.
