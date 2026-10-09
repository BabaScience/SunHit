/**
 * Ingest-time quality flags. Bad readings are flagged, never silently dropped.
 * Checks that need the sun position (night-time noise, above clear-sky) and history
 * (stuck values) run in the gate, which also treats flagged readings as suspect.
 */
export const FLAG_NEGATIVE = 1;
export const FLAG_ABOVE_SENSOR_MAX = 2; // VEML7700 tops out around 120 klx
export const FLAG_FUTURE_TS = 4; // more than 5 min ahead of the server clock
export const FLAG_STALE_TS = 8; // older than 7 days when received

export const LUX_SENSOR_MAX = 150_000;

export interface Reading {
  ts_utc: string;
  lux?: number | undefined;
  sun?: boolean | undefined;
}

export interface ObservationRow {
  ts: Date;
  kind: "lux" | "tap";
  value: number;
  flags: number;
}

export function toRows(readings: Reading[], now: Date = new Date()): ObservationRow[] {
  const rows: ObservationRow[] = [];
  for (const r of readings) {
    const ts = new Date(r.ts_utc);
    let tsFlags = 0;
    if (ts.getTime() - now.getTime() > 5 * 60_000) tsFlags |= FLAG_FUTURE_TS;
    if (now.getTime() - ts.getTime() > 7 * 86_400_000) tsFlags |= FLAG_STALE_TS;
    if (r.lux !== undefined) {
      let flags = tsFlags;
      if (r.lux < 0) flags |= FLAG_NEGATIVE;
      if (r.lux > LUX_SENSOR_MAX) flags |= FLAG_ABOVE_SENSOR_MAX;
      rows.push({ ts, kind: "lux", value: r.lux, flags });
    }
    if (r.sun !== undefined) {
      rows.push({ ts, kind: "tap", value: r.sun ? 1 : 0, flags: tsFlags });
    }
  }
  return rows;
}
