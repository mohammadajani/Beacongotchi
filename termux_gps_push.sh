#!/data/data/com.termux/files/usr/bin/bash
#
# Push phone GPS location to the RPi wardrive script over the local
# hotspot network. Run this ON YOUR PHONE, inside Termux.
#
# Requirements:
#   pkg install termux-api curl
#   Also install the "Termux:API" companion app (F-Droid or Play Store)
#   and grant it location permission.
#
# Setup:
#   1. Connect the Pi to your phone's hotspot as normal.
#   2. On the Pi, run `hostname -I` to get its IP on that hotspot network.
#   3. Set PI_IP below to that address.

PI_IP="192.168.1.X"   # <-- set this to the Pi's IP address
PI_PORT="8000"
INTERVAL=5             # seconds between GPS pushes

echo "Pushing GPS to http://$PI_IP:$PI_PORT/gps every ${INTERVAL}s..."

while true; do
  loc=$(termux-location -p gps -r once 2>/dev/null)

  if [ -n "$loc" ]; then
    lat=$(echo "$loc" | grep -o '"latitude": *[^,}]*' | grep -o '[-0-9.]*$')
    lon=$(echo "$loc" | grep -o '"longitude": *[^,}]*' | grep -o '[-0-9.]*$')

    if [ -n "$lat" ] && [ -n "$lon" ]; then
      curl -s -m 5 -X POST "http://$PI_IP:$PI_PORT/gps" \
        -H "Content-Type: application/json" \
        -d "{\"lat\": $lat, \"lon\": $lon}" > /dev/null
      echo "Sent: $lat, $lon"
    else
      echo "Got a location response but couldn't parse lat/lon"
    fi
  else
    echo "No location fix yet..."
  fi

  sleep "$INTERVAL"
done
