# Reference sensor (ESP32 + VEML7700)

Ground truth for the Phase 0 gate: a lux reading every 30 s from the exact surface a spot represents.

## Parts (about €10–15 each)
- ESP32 dev board (any `esp32dev`-compatible board)
- VEML7700 breakout. Reads up to about 120 klx. **Not BH1750**: it saturates around 65 klx, and direct sun
  is about 100 klx, so the clean sun/shade step disappears.
- USB power from inside the house.

Wiring: VEML7700 `VIN` → 3V3, `GND` → GND, `SDA` → GPIO21, `SCL` → GPIO22.

## Mounting
- On the real surface, facing the same way (a balcony floor sensor lies flat; a window sensor stands vertical).
- Put a small diffuser cap over the sensor (a piece of white PTFE or a ping-pong-ball half) and wipe it now
  and then. A dirty or hot sensor makes the model look worse than it is.

## Setup
1. Register the spot and a device (the token is shown once):
   ```bash
   curl -X POST localhost:3000/v1/spots -H 'content-type: application/json' \
     -d '{"lat":41.9,"lon":12.48,"height_above_ground_m":9,"tilt_deg":0,"facing_azimuth_deg":200,"label":"balcony A"}'
   curl -X POST localhost:3000/v1/spots/sp_xxx/devices -H 'content-type: application/json' -d '{"label":"esp32 A"}'
   ```
2. `cp include/secrets.example.h include/secrets.h` and fill in Wi-Fi, `API_BASE_URL`, `SPOT_ID`, `DEVICE_TOKEN`.
3. Flash with PlatformIO: `pio run -t upload && pio device monitor`.

## Reaching the api
Phase 0 has no real auth, so the api listens on `127.0.0.1` by default. For a sensor on the same LAN, start
the stack with `API_BIND=<your LAN IP>`. For sensors on other people's balconies, use a private network
such as Tailscale/WireGuard. Never expose the Phase 0 api to the internet.

## Zero-hardware fallback
An old phone taking a photo every minute. Label the sun-hit start/end times by hand in a CSV and pass it to
the gate with `--labels` (see `scripts/gate.sh`).
