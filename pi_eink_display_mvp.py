#!/usr/bin/env python3
"""
MVP: reads WiFi/BT scan results from the BW16 over USB-serial
(connected via micro-USB-to-USB-A OTG) and shows running counts
on the e-paper display.

IMPORTANT: swap the epd_driver import below for your exact panel
model (Waveshare model number, or whatever driver your display uses).

BT parsing note: the BW16 firmware doesn't emit structured BT lines -
it lets AmebaD's default BLE scan handler print its own MAC/RSSI text
to Serial inside a START_BT / END_BT block. We just regex out any MAC
address we see in that block rather than depending on the exact
default print format (which may vary by core version).
"""

import csv
import json
import os
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import serial
from PIL import Image, ImageDraw, ImageFont

# ---- adjust these for your setup ----
SERIAL_PORT = "/dev/ttyUSB0"   # check with `ls /dev/tty*` after plugging in BW16
BAUD = 115200

WIFI_CSV_PATH = "wifi_log.csv"
BT_CSV_PATH = "bt_log.csv"
SETTINGS_PATH = "settings.json"
CUSTOM_BLACK_PATH = "custom_black.png"
CUSTOM_RED_PATH = "custom_red.png"

DEFAULT_SETTINGS = {
    "refresh_mode": "immediate",       # "immediate" | "interval" | "off"
    "refresh_interval_seconds": 60,
    "blank_content": "white",          # "white" | "black" | "custom" (used when refresh_mode == "off")
}

GPS_HTTP_PORT = 8000        # Termux phone posts location here: http://<pi-ip>:8000/gps
GPS_STALE_AFTER = 15        # seconds - if no update in this long, treat as "no GPS data"

# 2.13" B model = red/black/white panel -> epd2in13b_V4 driver, NOT the
# plain monochrome epd2in13_V4. If your exact board is an older revision
# (V2/V3) and this import fails, check `ls ~/e-Paper/.../waveshare_epd/`
# for the closest matching epd2in13b_* filename and swap it in below.
from waveshare_epd import epd2in13b_V4 as epd_driver

FONT = ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18
)

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
RSSI_RE = re.compile(r"RSSI[:\s]*(-?\d+)", re.IGNORECASE)

# Shared GPS state, updated by the HTTP server thread, read by the main loop
gps_lock = threading.Lock()
gps_state = {"lat": None, "lon": None, "last_update": 0.0}


class GPSRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/gps":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            lat = float(data["lat"])
            lon = float(data["lon"])
            with gps_lock:
                gps_state["lat"] = lat
                gps_state["lon"] = lon
                gps_state["last_update"] = time.monotonic()
            self.send_response(200)
            self.end_headers()
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, format, *args):
        pass  # silence default request logging to keep the terminal readable


def start_gps_server():
    server = ThreadingHTTPServer(("0.0.0.0", GPS_HTTP_PORT), GPSRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"GPS server listening on 0.0.0.0:{GPS_HTTP_PORT}/gps")


def get_current_gps():
    """Returns (lat, lon) if a fix arrived within GPS_STALE_AFTER seconds, else (None, None)."""
    with gps_lock:
        if gps_state["lat"] is None:
            return None, None
        if time.monotonic() - gps_state["last_update"] > GPS_STALE_AFTER:
            return None, None
        return gps_state["lat"], gps_state["lon"]


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def open_csv_writer(path, header):
    """Open a CSV file in append mode, writing the header only if it's new."""
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    f = open(path, "a", newline="")
    writer = csv.writer(f)
    if is_new:
        writer.writerow(header)
        f.flush()
    return f, writer


def init_display():
    epd = epd_driver.EPD()
    epd.init()
    epd.Clear()
    return epd


def draw_stats(epd, wifi_count, bt_count, gps_lat, gps_lon):
    # Color e-paper panels use two separate 1-bit buffers (black + red)
    # composited by the controller, and only support full refresh - there's
    # no displayPartial() on this panel type.
    black_image = Image.new("1", (epd.height, epd.width), 255)
    red_image = Image.new("1", (epd.height, epd.width), 255)
    draw_black = ImageDraw.Draw(black_image)
    draw_red = ImageDraw.Draw(red_image)

    draw_red.text((5, 5), "Wardrive MVP", font=FONT, fill=0)
    draw_black.text((5, 35), f"WiFi APs: {wifi_count}", font=FONT, fill=0)
    draw_black.text((5, 65), f"BT devices: {bt_count}", font=FONT, fill=0)
    if gps_lat is not None:
        gps_line = f"GPS: {gps_lat:.5f},{gps_lon:.5f}"
    else:
        gps_line = "GPS: no data"
    draw_black.text((5, 95), gps_line, font=FONT, fill=0)
    draw_black.text((5, 125), datetime.now().strftime("%H:%M:%S"), font=FONT, fill=0)

    epd.display(epd.getbuffer(black_image), epd.getbuffer(red_image))


def show_blank(epd, blank_content):
    size = (epd.height, epd.width)
    if blank_content == "black":
        black_image = Image.new("1", size, 0)
        red_image = Image.new("1", size, 255)
    elif blank_content == "custom":
        try:
            black_image = Image.open(CUSTOM_BLACK_PATH).convert("1")
            red_image = Image.open(CUSTOM_RED_PATH).convert("1")
            if black_image.size != size:
                black_image = black_image.resize(size)
            if red_image.size != size:
                red_image = red_image.resize(size)
        except Exception as e:
            print(f"Couldn't load custom blank image ({e}), falling back to white")
            black_image = Image.new("1", size, 255)
            red_image = Image.new("1", size, 255)
    else:  # "white" or unrecognized -> default to white
        black_image = Image.new("1", size, 255)
        red_image = Image.new("1", size, 255)

    epd.display(epd.getbuffer(black_image), epd.getbuffer(red_image))


def main():
    epd = init_display()
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)

    start_gps_server()

    wifi_csv_file, wifi_csv = open_csv_writer(
        WIFI_CSV_PATH, ["timestamp", "ssid", "rssi", "encryption", "lat", "lon"]
    )
    bt_csv_file, bt_csv = open_csv_writer(
        BT_CSV_PATH, ["timestamp", "mac", "rssi", "lat", "lon"]
    )

    seen_wifi = set()
    seen_bt = set()
    in_bt_block = False
    last_displayed_state = None   # (wifi_count, bt_count, has_gps_fix) currently on screen (immediate mode)
    last_display_time = 0.0       # monotonic time of last actual e-paper write (interval mode)
    last_blank_shown = None       # which blank_content is currently on screen (off mode)

    print("Listening on", SERIAL_PORT)

    while True:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode(errors="ignore").strip()
        if not line:
            continue

        lat, lon = get_current_gps()

        if line.startswith("WIFI,"):
            parts = line.split(",", 3)
            if len(parts) == 4:
                _, ssid, rssi, encryption = parts
                seen_wifi.add(ssid)
                wifi_csv.writerow([datetime.now().isoformat(), ssid, rssi, encryption, lat, lon])
                wifi_csv_file.flush()

        elif line == "START_BT":
            in_bt_block = True

        elif line == "END_BT":
            in_bt_block = False
            gps_note = f"GPS {lat:.5f},{lon:.5f}" if lat is not None else "GPS none"
            print(f"WiFi: {len(seen_wifi)}  BT: {len(seen_bt)}  {gps_note}")

            settings = load_settings()
            mode = settings["refresh_mode"]

            if mode == "off":
                blank_content = settings["blank_content"]
                if blank_content != last_blank_shown:
                    show_blank(epd, blank_content)
                    last_blank_shown = blank_content
                # so switching back to immediate/interval forces a fresh redraw
                last_displayed_state = None
            else:
                last_blank_shown = None
                now = time.monotonic()
                current_state = (len(seen_wifi), len(seen_bt), lat is not None)

                if mode == "interval":
                    interval = settings["refresh_interval_seconds"]
                    if now - last_display_time >= interval:
                        draw_stats(epd, len(seen_wifi), len(seen_bt), lat, lon)
                        last_display_time = now
                        last_displayed_state = current_state
                else:  # "immediate" - GPS coordinates drift slightly on every
                       # fix, so we track fix/no-fix rather than exact lat/lon
                       # to avoid refreshing (and flashing) on GPS jitter alone.
                    if current_state != last_displayed_state:
                        draw_stats(epd, len(seen_wifi), len(seen_bt), lat, lon)
                        last_displayed_state = current_state
                        last_display_time = now

        elif in_bt_block:
            match = MAC_RE.search(line)
            if match:
                mac = match.group(1)
                rssi_match = RSSI_RE.search(line)
                bt_rssi = rssi_match.group(1) if rssi_match else ""
                seen_bt.add(mac)
                bt_csv.writerow([datetime.now().isoformat(), mac, bt_rssi, lat, lon])
                bt_csv_file.flush()


if __name__ == "__main__":
    main()
