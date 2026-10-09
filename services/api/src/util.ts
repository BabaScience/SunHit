import { createHash, randomBytes } from "node:crypto";
import { find as findTimezone } from "geo-tz";

const ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"; // lowercase Crockford base32

export function newId(prefix: string, length = 12): string {
  const bytes = randomBytes(length);
  let out = "";
  for (const b of bytes) out += ALPHABET[b % 32];
  return `${prefix}_${out}`;
}

export function newToken(): string {
  return `dt_${randomBytes(24).toString("base64url")}`;
}

export function sha256(s: string): string {
  return createHash("sha256").update(s).digest("hex");
}

export function timezoneAt(lat: number, lon: number): string {
  return findTimezone(lat, lon)[0] ?? "UTC";
}

/** Today's date (YYYY-MM-DD) in a timezone. */
export function todayIn(tz: string, now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Inclusive list of dates between from and to (YYYY-MM-DD), at most maxDays. */
export function dateRange(from: string, to: string, maxDays: number): string[] {
  if (!DATE_RE.test(from) || !DATE_RE.test(to)) throw new RangeError("dates must be YYYY-MM-DD");
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) throw new RangeError("invalid date");
  if (end < start) throw new RangeError("'to' is before 'from'");
  const days = Math.round((end - start) / 86_400_000) + 1;
  if (days > maxDays) throw new RangeError(`at most ${maxDays} days per request`);
  return Array.from({ length: days }, (_, i) => new Date(start + i * 86_400_000).toISOString().slice(0, 10));
}

/** Collapses a per-azimuth source array into runs. */
export function sourceSegments(source: string[], stepDeg: number) {
  const out: { from_azimuth_deg: number; to_azimuth_deg: number; source: string }[] = [];
  source.forEach((s, i) => {
    const last = out[out.length - 1];
    if (last && last.source === s) last.to_azimuth_deg = (i + 1) * stepDeg;
    else out.push({ from_azimuth_deg: i * stepDeg, to_azimuth_deg: (i + 1) * stepDeg, source: s });
  });
  return out;
}
