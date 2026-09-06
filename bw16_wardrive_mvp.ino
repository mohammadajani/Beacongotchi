/*
 * BW16 MVP Wardrive Sniffer (corrected for AmebaD's actual WiFi + BLE APIs)
 * ---------------------------------------------------------
 * Alternates between a WiFi network scan and a BLE scan window,
 * printing results as simple lines over Serial (USB-CDC) for the
 * RPi Zero 2W to parse and show on the e-paper display.
 *
 * Board: Ai-Thinker BW16 (RTL8720DN / AmebaD arduino-cli core)
 *
 * WiFi notes:
 * - AmebaD's WiFi library follows the old Arduino "WiFi shield" API
 *   (WiFi101-style), NOT the ESP32 WiFi API. No WiFi.mode(), no
 *   WiFi.disconnect() needed before scanning, no WiFi.scanDelete().
 * - WiFi.BSSID() only reports the currently-connected AP's MAC -
 *   it's not indexable per scan result like on ESP32/ESP8266, so
 *   scanned networks are identified/deduped by SSID here instead.
 *   (Two different APs broadcasting the same SSID would undercount -
 *   fine for an MVP, worth revisiting later if that matters to you.)
 *
 * BLE notes:
 * - Uses AmebaD's native BLE library: global `BLE` object from
 *   BLEDevice.h + BLEScan.h (not ESP32/NimBLE-style classes).
 * - No custom scan callback registered - the library's default
 *   handler prints each discovered device (MAC + RSSI) to Serial
 *   for us as it scans; the Pi script regex-matches MAC addresses
 *   out of that block.
 *
 * NOTE: WiFi.scanNetworks() is a beacon/network scan (like a phone's
 * WiFi picker), not raw promiscuous packet capture. Good enough for
 * an MVP; handshake/packet-level sniffing is a v2 step.
 *
 * IMPORTANT: full-band scanNetworks() also sweeps 5GHz, including
 * DFS channels and channel 165 - which is known to be flaky on
 * RTL8720-family radios and can hang the scan indefinitely. We
 * restrict scanning to 2.4GHz only via the lower-level
 * wifi_set_pscan_chan() SDK call (which WiFi.h itself is built on
 * top of) to avoid that entirely - we don't need 5GHz for this
 * anyway since it's a much smaller slice of real-world traffic.
 * ---------------------------------------------------------
 */

#include <WiFi.h>
#include "BLEDevice.h"
#include "BLEScan.h"
extern "C" {
  #include "wifi_conf.h"
}

#define BLE_SCAN_MS 6000

uint8_t chan2g[13] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13};

void restrictScanTo2GHz() {
  uint8_t config = PSCAN_ENABLE;
  wifi_set_pscan_chan(chan2g, &config, 13);
}

void setup() {
  Serial.begin(115200);
  delay(2000);

  restrictScanTo2GHz();

  BLE.init();
  BLE.configScan()->setScanMode(GAP_SCAN_MODE_ACTIVE);
  BLE.configScan()->setScanInterval(500);  // start a scan every 500ms
  BLE.configScan()->setScanWindow(250);    // each scan lasts 250ms
  BLE.beginCentral(0);

  Serial.println("READY");
}

void doWifiScan() {
  int n = WiFi.scanNetworks();
  Serial.print("WIFI_COUNT,");
  Serial.println(n);

  for (int i = 0; i < n; i++) {
    Serial.print("WIFI,");
    Serial.print(WiFi.SSID(i));
    Serial.print(",");
    Serial.print(WiFi.RSSI(i));
    Serial.print(",");
    Serial.println(encTypeToStr(WiFi.encryptionType(i)));
  }
  Serial.println("END_WIFI");
}

// wl_enc_type values match the Arduino WiFi-shield-style enum AmebaD reuses
String encTypeToStr(uint8_t t) {
  switch (t) {
    case 2: return "TKIP(WPA)";
    case 4: return "CCMP(WPA2)";
    case 5: return "WEP";
    case 7: return "NONE(OPEN)";
    case 8: return "AUTO";
    default: return "UNKNOWN";
  }
}

void doBleScan() {
  Serial.println("START_BT");
  // No callback registered -> default printScanInfo() prints each
  // device's MAC/RSSI to Serial as it's discovered during this window.
  BLE.configScan()->startScan(BLE_SCAN_MS);
  delay(BLE_SCAN_MS + 300);  // let the scan window finish before moving on
  Serial.println("END_BT");
}

void loop() {
  doWifiScan();
  delay(200);
  doBleScan();
  delay(200);
}
