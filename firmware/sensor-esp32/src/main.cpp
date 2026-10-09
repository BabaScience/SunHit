// Sun-Hit reference sensor.
// Reads lux every 30 s and posts {ts_utc, lux} batches to POST /v1/spots/{SPOT_ID}/observations
// with the static device token. Readings are buffered (about 2 h) while Wi-Fi or the api is down.

#include <Adafruit_VEML7700.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <time.h>

#include "secrets.h"

static const uint32_t SAMPLE_MS = 30 * 1000;
static const size_t BUFFER_SIZE = 240;  // 2 h at 30 s
static const size_t BATCH_SIZE = 40;
static const uint32_t HTTP_TIMEOUT_MS = 10 * 1000;

struct Reading {
  time_t ts;
  float lux;
};

Adafruit_VEML7700 veml;
Reading buffer[BUFFER_SIZE];
size_t head = 0;   // oldest reading
size_t count = 0;  // readings waiting to be sent
uint32_t lastSample = 0;
uint32_t lastWifiAttempt = 0;

void push(const Reading& r) {
  if (count == BUFFER_SIZE) {  // full: drop the oldest
    head = (head + 1) % BUFFER_SIZE;
    count--;
  }
  buffer[(head + count) % BUFFER_SIZE] = r;
  count++;
}

bool timeSynced() { return time(nullptr) > 1700000000; }  // after Nov 2023

void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiAttempt < 15000 && lastWifiAttempt != 0) return;
  lastWifiAttempt = millis();
  Serial.println("wifi: connecting");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void isoUtc(time_t t, char* out, size_t n) {
  struct tm tm;
  gmtime_r(&t, &tm);
  strftime(out, n, "%Y-%m-%dT%H:%M:%SZ", &tm);
}

// Sends up to BATCH_SIZE buffered readings. Returns true if the batch left the buffer.
bool flushBatch() {
  if (count == 0 || WiFi.status() != WL_CONNECTED) return false;
  size_t n = count < BATCH_SIZE ? count : BATCH_SIZE;

  JsonDocument doc;
  JsonArray readings = doc["readings"].to<JsonArray>();
  char ts[25];
  for (size_t i = 0; i < n; i++) {
    const Reading& r = buffer[(head + i) % BUFFER_SIZE];
    isoUtc(r.ts, ts, sizeof ts);
    JsonObject o = readings.add<JsonObject>();
    o["ts_utc"] = ts;
    o["lux"] = r.lux;
  }
  String body;
  serializeJson(doc, body);

  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.begin(String(API_BASE_URL) + "/v1/spots/" + SPOT_ID + "/observations");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  int code = http.POST(body);
  String resp = http.getString();
  http.end();

  if (code >= 200 && code < 300) {
    head = (head + n) % BUFFER_SIZE;
    count -= n;
    Serial.printf("sent %u readings: %s\n", (unsigned)n, resp.c_str());
    return true;
  }
  if (code >= 400 && code < 500 && code != 408 && code != 429) {
    // The api rejected the data itself (bad token, wrong spot, invalid body): retrying won't help.
    Serial.printf("rejected (%d), dropping batch: %s\n", code, resp.c_str());
    head = (head + n) % BUFFER_SIZE;
    count -= n;
    return true;
  }
  Serial.printf("send failed (%d), keeping %u buffered\n", code, (unsigned)count);
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(200);
  if (!veml.begin()) {
    Serial.println("VEML7700 not found: check wiring (SDA=21, SCL=22, 3V3, GND)");
    while (true) delay(1000);
  }
  ensureWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
}

void loop() {
  ensureWifi();
  if (millis() - lastSample >= SAMPLE_MS || lastSample == 0) {
    lastSample = millis();
    if (timeSynced()) {
      // Auto gain/integration keeps the full range: shade (a few klx) to direct sun (about 100 klx).
      float lux = veml.readLux(VEML_LUX_AUTO);
      push({time(nullptr), lux});
      Serial.printf("lux %.0f (buffered %u)\n", lux, (unsigned)count);
    } else {
      Serial.println("waiting for NTP time");
    }
  }
  // Drain the backlog a few batches at a time.
  for (int i = 0; i < 3 && flushBatch(); i++) {
  }
  delay(200);
}
