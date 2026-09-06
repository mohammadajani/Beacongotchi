#!/usr/bin/env python3
"""
Web dashboard for the wardrive MVP.

Run THIS instead of pi_eink_display_mvp.py directly - it launches and
manages that script as a child process, so uploading a new version can
restart it cleanly. It also tails wifi_log.csv / bt_log.csv for a live
view, stores display settings in settings.json (which the capture
script reads every cycle), handles image uploads for the "blank
display" state, and serves CSV exports.

    python3 dashboard.py

Requires: pip3 install flask --break-system-packages
(Pillow and pyserial should already be installed from the capture script setup)
"""

import json
import os
import subprocess
import sys
import threading

from flask import Flask, request, jsonify, send_file, Response
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_SCRIPT = os.path.join(BASE_DIR, "pi_eink_display_mvp.py")
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
WIFI_CSV = os.path.join(BASE_DIR, "wifi_log.csv")
BT_CSV = os.path.join(BASE_DIR, "bt_log.csv")
CUSTOM_BLACK_PATH = os.path.join(BASE_DIR, "custom_black.png")
CUSTOM_RED_PATH = os.path.join(BASE_DIR, "custom_red.png")

# Panel resolution for the 2.13" B (red/black/white) model, landscape
# orientation - matches the capture script's Image.new("1", (epd.height,
# epd.width)) convention. If you swap panels, update this to match.
PANEL_SIZE = (250, 122)  # (width, height)

DEFAULT_SETTINGS = {
    "refresh_mode": "immediate",       # "immediate" | "interval" | "off"
    "refresh_interval_seconds": 60,
    "blank_content": "white",          # "white" | "black" | "custom"
}

app = Flask(__name__)

capture_proc = None
capture_lock = threading.Lock()


# ---------------- settings ----------------

def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


# ---------------- capture process management ----------------

def start_capture():
    global capture_proc
    with capture_lock:
        if capture_proc is not None and capture_proc.poll() is None:
            return
        capture_proc = subprocess.Popen([sys.executable, CAPTURE_SCRIPT], cwd=BASE_DIR)


def restart_capture():
    global capture_proc
    with capture_lock:
        if capture_proc is not None and capture_proc.poll() is None:
            capture_proc.terminate()
            try:
                capture_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                capture_proc.kill()
        capture_proc = subprocess.Popen([sys.executable, CAPTURE_SCRIPT], cwd=BASE_DIR)


def capture_status():
    with capture_lock:
        if capture_proc is None:
            return "stopped"
        return "running" if capture_proc.poll() is None else "crashed"


# ---------------- log tailing ----------------

def tail_csv(path, n=25):
    if not os.path.exists(path):
        return {"header": [], "rows": []}
    with open(path) as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]
    if not lines:
        return {"header": [], "rows": []}
    header = lines[0].split(",")
    data_lines = lines[1:][-n:]
    rows = [l.split(",") for l in data_lines]
    return {"header": header, "rows": rows}


# ---------------- API ----------------

@app.route("/api/status")
def api_status():
    return jsonify({"capture": capture_status(), "settings": load_settings()})


@app.route("/api/logs")
def api_logs():
    n = int(request.args.get("n", 25))
    return jsonify({"wifi": tail_csv(WIFI_CSV, n), "bt": tail_csv(BT_CSV, n)})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(load_settings())

    data = request.get_json(force=True)
    settings = load_settings()

    if data.get("refresh_mode") in ("immediate", "interval", "off"):
        settings["refresh_mode"] = data["refresh_mode"]
    if "refresh_interval_seconds" in data:
        try:
            settings["refresh_interval_seconds"] = max(5, int(data["refresh_interval_seconds"]))
        except (TypeError, ValueError):
            pass
    if data.get("blank_content") in ("white", "black", "custom"):
        settings["blank_content"] = data["blank_content"]

    save_settings(settings)
    return jsonify(settings)


@app.route("/api/export/<kind>")
def api_export(kind):
    path = {"wifi": WIFI_CSV, "bt": BT_CSV}.get(kind)
    if path is None or not os.path.exists(path):
        return "Not found", 404
    return send_file(path, as_attachment=True)


@app.route("/api/upload_image", methods=["POST"])
def api_upload_image():
    if "image" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    f = request.files["image"]
    try:
        img = Image.open(f.stream).convert("RGB")
    except Exception as e:
        return jsonify({"error": f"couldn't read image: {e}"}), 400

    # Fit within panel size (never distort/crop), pad remainder with white
    img.thumbnail(PANEL_SIZE, Image.LANCZOS)
    canvas = Image.new("RGB", PANEL_SIZE, (255, 255, 255))
    x = (PANEL_SIZE[0] - img.width) // 2
    y = (PANEL_SIZE[1] - img.height) // 2
    canvas.paste(img, (x, y))

    # Snap every pixel to the nearest of white/black/red - this panel has
    # no grayscale, so anything else (photos, gradients) will look rough.
    black_layer = Image.new("1", PANEL_SIZE, 255)
    red_layer = Image.new("1", PANEL_SIZE, 255)
    src = canvas.load()
    bp = black_layer.load()
    rp = red_layer.load()

    for yy in range(PANEL_SIZE[1]):
        for xx in range(PANEL_SIZE[0]):
            r, g, b = src[xx, yy]
            d_white = (255 - r) ** 2 + (255 - g) ** 2 + (255 - b) ** 2
            d_black = r ** 2 + g ** 2 + b ** 2
            d_red = (255 - r) ** 2 + g ** 2 + b ** 2
            best = min(("white", d_white), ("black", d_black), ("red", d_red), key=lambda t: t[1])[0]
            if best == "black":
                bp[xx, yy] = 0
            elif best == "red":
                rp[xx, yy] = 0

    black_layer.save(CUSTOM_BLACK_PATH)
    red_layer.save(CUSTOM_RED_PATH)
    return jsonify({"ok": True})


@app.route("/api/upload_script", methods=["POST"])
def api_upload_script():
    if "script" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    f = request.files["script"]
    if not f.filename.endswith(".py"):
        return jsonify({"error": "must be a .py file"}), 400

    content = f.read()
    try:
        compile(content, "uploaded_script.py", "exec")
    except SyntaxError as e:
        return jsonify({"error": f"syntax error in uploaded script: {e}"}), 400

    if os.path.exists(CAPTURE_SCRIPT):
        os.replace(CAPTURE_SCRIPT, CAPTURE_SCRIPT + ".bak")
    with open(CAPTURE_SCRIPT, "wb") as out:
        out.write(content)

    restart_capture()
    return jsonify({"ok": True, "restarted": True})


@app.route("/api/restart_capture", methods=["POST"])
def api_restart_capture():
    restart_capture()
    return jsonify({"ok": True})


# ---------------- UI ----------------

DASHBOARD_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Wardrive Dashboard</title>
<style>
  body { font-family: sans-serif; max-width: 1000px; margin: 20px auto; padding: 0 15px; }
  h1 { margin-bottom: 4px; }
  h2 { border-bottom: 2px solid #333; padding-bottom: 5px; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; word-break: break-all; }
  th { background: #eee; }
  .row { display: flex; gap: 30px; flex-wrap: wrap; }
  .col { flex: 1; min-width: 320px; }
  fieldset { margin-bottom: 20px; }
  .status-running { color: green; font-weight: bold; }
  .status-stopped, .status-crashed { color: #c00; font-weight: bold; }
  .note { font-size: 12px; color: #555; }
  button { padding: 6px 14px; margin-top: 8px; cursor: pointer; }
  .log-scroll { max-height: 400px; overflow-y: auto; }
</style>
</head>
<body>
<h1>Wardrive Dashboard</h1>
<p>Capture script status: <span id="status">...</span></p>

<fieldset>
  <legend>Display refresh settings</legend>
  <label>Refresh mode:
    <select id="refresh_mode">
      <option value="immediate">Immediate - update as soon as WiFi/BT counts change</option>
      <option value="interval">Interval - update at most every N seconds</option>
      <option value="off">Off - freeze the display and show a blank/graphic instead</option>
    </select>
  </label>
  <br><br>
  <label>Interval (seconds, used when mode = interval):
    <input type="number" id="refresh_interval" min="5" value="60" style="width:80px">
  </label>
  <br><br>
  <label>When mode = off, show:
    <select id="blank_content">
      <option value="white">Blank white</option>
      <option value="black">Blank black</option>
      <option value="custom">Uploaded graphic (see below)</option>
    </select>
  </label>
  <br><br>
  <button onclick="saveSettings()">Save settings</button>
  <span id="settings_saved"></span>
  <p class="note">Capture (BW16 scanning + CSV logging) keeps running in the background regardless of these settings - this only controls what the e-paper panel shows.</p>
</fieldset>

<fieldset>
  <legend>Upload display graphic</legend>
  <p class="note">
    The panel is 250x122 pixels and only supports <b>3 colors: white, black, and red</b> - no grayscale or gradients.
    Any image you upload (PNG, JPG, BMP, GIF) is resized to fit (padded with white, never stretched or cropped)
    and every pixel is snapped to the nearest of those 3 colors. Simple logos, line art, or high-contrast
    graphics work best; photos will look rough/dithered. This only appears when refresh mode is "Off" and
    "When off, show" is set to "Uploaded graphic".
  </p>
  <input type="file" id="image_file" accept="image/*">
  <button onclick="uploadImage()">Upload</button>
  <span id="image_status"></span>
</fieldset>

<fieldset>
  <legend>Update capture script</legend>
  <p class="note">
    Upload a new pi_eink_display_mvp.py to replace the running one. It's syntax-checked before being
    applied, the previous version is kept as a .bak file alongside it, and the capture process restarts
    automatically once the update is applied.
  </p>
  <input type="file" id="script_file" accept=".py">
  <button onclick="uploadScript()">Upload &amp; restart</button>
  <span id="script_status"></span>
</fieldset>

<fieldset>
  <legend>Export logs</legend>
  <a href="/api/export/wifi">Download wifi_log.csv</a> &nbsp;|&nbsp;
  <a href="/api/export/bt">Download bt_log.csv</a>
  <br><br>
  <button onclick="restartCapture()">Restart capture script manually</button>
</fieldset>

<div class="row">
  <div class="col">
    <h2>Live WiFi log</h2>
    <div class="log-scroll"><table id="wifi_table"><thead></thead><tbody></tbody></table></div>
  </div>
  <div class="col">
    <h2>Live Bluetooth log</h2>
    <div class="log-scroll"><table id="bt_table"><thead></thead><tbody></tbody></table></div>
  </div>
</div>

<script>
async function refreshStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  const el = document.getElementById('status');
  el.textContent = d.capture;
  el.className = 'status-' + d.capture;

  document.getElementById('refresh_mode').value = d.settings.refresh_mode;
  document.getElementById('refresh_interval').value = d.settings.refresh_interval_seconds;
  document.getElementById('blank_content').value = d.settings.blank_content;
}

function fillTable(tableId, data) {
  const table = document.getElementById(tableId);
  const thead = table.querySelector('thead');
  const tbody = table.querySelector('tbody');
  if (!data || !data.header || data.header.length === 0) { thead.innerHTML = ''; tbody.innerHTML = ''; return; }
  thead.innerHTML = '<tr>' + data.header.map(h => '<th>' + h + '</th>').join('') + '</tr>';
  tbody.innerHTML = data.rows.slice().reverse().map(
    row => '<tr>' + row.map(c => '<td>' + (c || '') + '</td>').join('') + '</tr>'
  ).join('');
}

async function refreshLogs() {
  const r = await fetch('/api/logs?n=30');
  const d = await r.json();
  fillTable('wifi_table', d.wifi);
  fillTable('bt_table', d.bt);
}

async function saveSettings() {
  const body = {
    refresh_mode: document.getElementById('refresh_mode').value,
    refresh_interval_seconds: document.getElementById('refresh_interval').value,
    blank_content: document.getElementById('blank_content').value,
  };
  await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  document.getElementById('settings_saved').textContent = 'Saved.';
  setTimeout(() => document.getElementById('settings_saved').textContent = '', 2000);
}

async function uploadImage() {
  const fileInput = document.getElementById('image_file');
  if (!fileInput.files.length) return;
  const form = new FormData();
  form.append('image', fileInput.files[0]);
  document.getElementById('image_status').textContent = 'Uploading...';
  const r = await fetch('/api/upload_image', { method: 'POST', body: form });
  const d = await r.json();
  document.getElementById('image_status').textContent = d.ok ? 'Uploaded.' : ('Error: ' + d.error);
}

async function uploadScript() {
  const fileInput = document.getElementById('script_file');
  if (!fileInput.files.length) return;
  const form = new FormData();
  form.append('script', fileInput.files[0]);
  document.getElementById('script_status').textContent = 'Uploading...';
  const r = await fetch('/api/upload_script', { method: 'POST', body: form });
  const d = await r.json();
  document.getElementById('script_status').textContent = d.ok ? 'Updated and restarted.' : ('Error: ' + d.error);
}

async function restartCapture() {
  await fetch('/api/restart_capture', { method: 'POST' });
  refreshStatus();
}

refreshStatus();
refreshLogs();
setInterval(refreshStatus, 5000);
setInterval(refreshLogs, 4000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


if __name__ == "__main__":
    load_settings()
    start_capture()
    app.run(host="0.0.0.0", port=5000, threaded=True)
