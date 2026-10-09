import { describe, expect, it } from "vitest";
import {
  FLAG_ABOVE_SENSOR_MAX,
  FLAG_FUTURE_TS,
  FLAG_NEGATIVE,
  FLAG_STALE_TS,
  toRows,
} from "../src/observations.js";
import { resolveDates } from "../src/routes/spots.js";
import { dateRange, newId, sourceSegments, timezoneAt } from "../src/util.js";

describe("observations flags", () => {
  const now = new Date("2026-10-10T12:00:00Z");

  it("flags bad readings without dropping them", () => {
    const rows = toRows(
      [
        { ts_utc: "2026-10-10T11:59:30Z", lux: 85_000 },
        { ts_utc: "2026-10-10T11:59:00Z", lux: -3 },
        { ts_utc: "2026-10-10T11:58:30Z", lux: 190_000 },
        { ts_utc: "2026-10-10T12:30:00Z", lux: 10 },
        { ts_utc: "2026-09-01T12:00:00Z", lux: 10 },
      ],
      now,
    );
    expect(rows.map((r) => r.flags)).toEqual([0, FLAG_NEGATIVE, FLAG_ABOVE_SENSOR_MAX, FLAG_FUTURE_TS, FLAG_STALE_TS]);
  });

  it("stores user taps as kind=tap", () => {
    const rows = toRows([{ ts_utc: "2026-10-10T11:00:00Z", sun: true }], now);
    expect(rows).toEqual([{ ts: new Date("2026-10-10T11:00:00Z"), kind: "tap", value: 1, flags: 0 }]);
  });
});

describe("dates", () => {
  it("expands inclusive ranges and caps their length", () => {
    expect(dateRange("2026-10-30", "2026-11-02", 31)).toEqual(["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
    expect(() => dateRange("2026-10-01", "2026-12-01", 31)).toThrow(/at most/);
    expect(() => dateRange("2026-10-02", "2026-10-01", 31)).toThrow(/before/);
  });

  it("resolves ?date, ?from/to and defaults to today in the spot timezone", () => {
    expect(resolveDates({ date: "2026-10-10" }, "Europe/Rome")).toEqual(["2026-10-10"]);
    expect(resolveDates({ from: "2026-10-10", to: "2026-10-11" }, "Europe/Rome")).toHaveLength(2);
    expect(resolveDates({}, "Europe/Rome")).toHaveLength(1);
    expect(() => resolveDates({ date: "10/10/2026" }, "Europe/Rome")).toThrow();
  });
});

describe("util", () => {
  it("finds the timezone of Rome", () => {
    expect(timezoneAt(41.9028, 12.4964)).toBe("Europe/Rome");
  });

  it("makes prefixed ids matching the contract pattern", () => {
    expect(newId("sp")).toMatch(/^sp_[a-z0-9]{12}$/);
  });

  it("collapses horizon sources into azimuth runs", () => {
    expect(sourceSegments(["terrain", "terrain", "building", "terrain"], 90)).toEqual([
      { from_azimuth_deg: 0, to_azimuth_deg: 180, source: "terrain" },
      { from_azimuth_deg: 180, to_azimuth_deg: 270, source: "building" },
      { from_azimuth_deg: 270, to_azimuth_deg: 360, source: "terrain" },
    ]);
  });
});
