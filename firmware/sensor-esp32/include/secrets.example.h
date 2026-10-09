// Copy to secrets.h (git-ignored) and fill in.
#pragma once

#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

// The api must be reachable from the sensor's network (see firmware/sensor-esp32/README.md).
#define API_BASE_URL "http://192.168.1.10:3000"

// From POST /v1/spots and POST /v1/spots/{id}/devices.
#define SPOT_ID "sp_xxxxxxxxxxxx"
#define DEVICE_TOKEN "dt_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
