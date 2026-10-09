import type { Horizon, Spot, Windows } from "@sunhit/contracts";
import maplibregl from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { solarPosition } from "./sun";

type Segment = Windows["days"][number]["segments"][number];

const ROME: [number, number] = [12.4769, 41.8986];
const COLORS: Record<string, string> = {
  sun: "var(--sun)",
  filtered: "var(--filtered)",
  building: "var(--building)",
  terrain: "var(--terrain)",
  surface: "var(--surface)",
  tree: "var(--tree)",
  user: "var(--accent)",
  none: "transparent",
};

function segColor(s: Segment) {
  if (s.type === "sun") return COLORS.sun;
  if (s.type === "filtered") return COLORS.filtered;
  return COLORS[s.by ?? "surface"];
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...init?.headers } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${r.status} ${body.error ?? ""}: ${body.message ?? r.statusText}`);
  return body as T;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const hhmm = (iso: string) => iso.slice(11, 16);
const today = () => new Date().toISOString().slice(0, 10);

export function App() {
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const [pos, setPos] = useState<{ lat: number; lon: number } | null>(null);
  const [height, setHeight] = useState(10);
  const [tilt, setTilt] = useState(0);
  const [facing, setFacing] = useState(180);
  const [label, setLabel] = useState("");
  const [date, setDate] = useState(today());
  const [spotId, setSpotId] = useState(() => new URLSearchParams(location.search).get("spot") ?? "");
  const [spot, setSpot] = useState<Spot | null>(null);
  const [horizon, setHorizon] = useState<Horizon | null>(null);
  const [windows, setWindows] = useState<Windows | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error?: boolean } | null>(null);

  useEffect(() => {
    if (!mapEl.current) return;
    const m = new maplibregl.Map({
      container: mapEl.current,
      center: ROME,
      zoom: 15,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 19,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
    });
    m.on("click", (e) => setPos({ lat: e.lngLat.lat, lon: e.lngLat.lng }));
    map.current = m;
    return () => m.remove();
  }, []);

  useEffect(() => {
    if (!pos || !map.current) return;
    if (!marker.current) {
      // A marker needs a position before it joins the map.
      marker.current = new maplibregl.Marker({ color: "#f2b705" }).setLngLat([pos.lon, pos.lat]).addTo(map.current);
    }
    marker.current.setLngLat([pos.lon, pos.lat]);
  }, [pos]);

  async function loadSpot(id: string, forDate = date) {
    setBusy(true);
    setStatus({ text: `Loading ${id}…` });
    try {
      let s = await api<Spot>(`/v1/spots/${id}`);
      for (let i = 0; s.horizon_status === "pending" && i < 60; i++) {
        setStatus({ text: "Building horizon…" });
        await sleep(1000);
        s = await api<Spot>(`/v1/spots/${id}`);
      }
      setSpot(s);
      setPos({ lat: s.lat, lon: s.lon });
      setHeight(s.height_above_ground_m);
      setTilt(s.tilt_deg);
      setFacing(s.facing_azimuth_deg);
      setLabel(s.label ?? "");
      map.current?.flyTo({ center: [s.lon, s.lat], zoom: 17 });
      history.replaceState(null, "", `?spot=${id}`);
      if (s.horizon_status !== "ready") {
        setHorizon(null);
        setWindows(null);
        setStatus({ text: `Horizon ${s.horizon_status}${s.horizon_error ? `: ${s.horizon_error}` : ""}`, error: true });
        return;
      }
      const [h, w] = await Promise.all([
        api<Horizon>(`/v1/spots/${id}/horizon`),
        api<Windows>(`/v1/spots/${id}/windows?date=${forDate}`),
      ]);
      setHorizon(h);
      setWindows(w);
      setStatus({ text: `Spot ${id} · building ${s.building_id ?? "none"} · ${s.timezone}` });
    } catch (e) {
      setStatus({ text: (e as Error).message, error: true });
    } finally {
      setBusy(false);
    }
  }

  async function register() {
    if (!pos) return;
    setBusy(true);
    try {
      const s = await api<Spot>("/v1/spots", {
        method: "POST",
        body: JSON.stringify({
          lat: pos.lat,
          lon: pos.lon,
          height_above_ground_m: height,
          tilt_deg: tilt,
          facing_azimuth_deg: facing % 360,
          ...(label ? { label } : {}),
        }),
      });
      setSpotId(s.id);
      await loadSpot(s.id);
    } catch (e) {
      setStatus({ text: (e as Error).message, error: true });
      setBusy(false);
    }
  }

  async function pointQuery() {
    if (!pos) return;
    setBusy(true);
    setStatus({ text: "Computing one-off point query…" });
    try {
      const q = new URLSearchParams({
        lat: String(pos.lat),
        lon: String(pos.lon),
        height_above_ground_m: String(height),
        tilt_deg: String(tilt),
        facing_azimuth_deg: String(facing % 360),
        date,
      });
      const w = await api<Windows>(`/v1/point?${q}`);
      setSpot(null);
      setHorizon(null);
      setWindows(w);
      setStatus({ text: "Point query (no stored horizon, not calibrated)" });
    } catch (e) {
      setStatus({ text: (e as Error).message, error: true });
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (spotId) void loadSpot(spotId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const day = windows?.days[0];

  return (
    <div className="layout">
      <div className="map">
        <div ref={mapEl} />
      </div>
      <div className="side">
        <h1>Sun-Hit debug</h1>
        <p className="hint">Click the map to place a spot, then register it or run a one-off point query.</p>
        <div className="grid">
          <label>
            Latitude
            <input value={pos?.lat.toFixed(6) ?? ""} readOnly />
          </label>
          <label>
            Longitude
            <input value={pos?.lon.toFixed(6) ?? ""} readOnly />
          </label>
          <label>
            Height above ground (m)
            <input type="number" min={0} step={0.5} value={height} onChange={(e) => setHeight(+e.target.value)} />
          </label>
          <label>
            Tilt
            <select value={tilt} onChange={(e) => setTilt(+e.target.value)}>
              <option value={0}>0° horizontal (floor, table)</option>
              <option value={30}>30° (panel)</option>
              <option value={90}>90° vertical (window, facade)</option>
            </select>
          </label>
          <label>
            Facing (° from north)
            <input type="number" min={0} max={359} value={facing} onChange={(e) => setFacing(+e.target.value)} />
          </label>
          <label>
            Date
            <input
              type="date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value);
                if (spot?.horizon_status === "ready") void loadSpot(spot.id, e.target.value);
              }}
            />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            Label (optional)
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. balcony 3rd floor" />
          </label>
        </div>
        <div className="row">
          <button disabled={!pos || busy} onClick={register}>
            Register spot
          </button>
          <button className="secondary" disabled={!pos || busy} onClick={pointQuery}>
            Point query
          </button>
        </div>
        <div className="row">
          <input
            className="mono"
            style={{ flex: 1 }}
            placeholder="sp_… existing spot id"
            value={spotId}
            onChange={(e) => setSpotId(e.target.value.trim())}
          />
          <button className="secondary" disabled={!spotId || busy} onClick={() => loadSpot(spotId)}>
            Load
          </button>
        </div>
        {status && <div className={`status ${status.error ? "error" : ""}`}>{status.text}</div>}

        {day && (
          <>
            <h2>Day · {day.date}</h2>
            <Timeline segments={day.segments} />
            <div className="legend">
              {["sun", "filtered", "building", "terrain", "surface"].map((k) => (
                <span key={k} style={{ "--c": COLORS[k] } as React.CSSProperties}>
                  {k}
                </span>
              ))}
            </div>
            <p className="stat">
              Direct sun <b>{day.expected_sun_minutes} min</b> · through trees {day.filtered_minutes} min ·{" "}
              {windows!.model_version} · horizon {windows!.horizon_confidence}
            </p>
            <table>
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>State</th>
                  <th>Ends by</th>
                </tr>
              </thead>
              <tbody>
                {day.segments.map((s, i) => (
                  <tr key={i}>
                    <td className="mono">{hhmm(s.start)}</td>
                    <td className="mono">{hhmm(s.end)}</td>
                    <td>
                      {s.type}
                      {s.by ? ` (${s.by})` : ""}
                      {s.transmissivity !== undefined ? ` T=${s.transmissivity}` : ""}
                    </td>
                    <td>{s.blocked_after_by ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {horizon && spot && (
          <>
            <h2>Horizon · {horizon.surface_version}</h2>
            <Polar horizon={horizon} spot={spot} date={date} segments={day?.segments ?? []} />
            <p className="hint">
              Building heights: <b>{horizon.height_confidence}</b> confidence (
              {Math.round(horizon.default_height_share * 100)}% default) · sources{" "}
              {[...new Set(horizon.height_source.filter(Boolean))].join(", ") || "none"} · ground{" "}
              {horizon.ground_elevation_m} m · eye {horizon.eye_elevation_m} m a.s.l. {horizon.notes.join(" ")}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

function Timeline({ segments }: { segments: Segment[] }) {
  if (!segments.length) return <p className="hint">No daylight segments.</p>;
  const t0 = Date.parse(segments[0]!.start);
  const t1 = Date.parse(segments[segments.length - 1]!.end);
  return (
    <div className="timeline" title={`${hhmm(segments[0]!.start)} – ${hhmm(segments[segments.length - 1]!.end)}`}>
      {segments.map((s, i) => (
        <div
          key={i}
          title={`${s.type}${s.by ? ` (${s.by})` : ""} ${hhmm(s.start)}–${hhmm(s.end)}`}
          style={{ width: `${((Date.parse(s.end) - Date.parse(s.start)) / (t1 - t0)) * 100}%`, background: segColor(s) }}
        />
      ))}
    </div>
  );
}

function Polar({ horizon, spot, date, segments }: { horizon: Horizon; spot: Spot; date: string; segments: Segment[] }) {
  const S = 420;
  const c = S / 2;
  const R = c - 22;
  const rOf = (el: number) => (R * (90 - Math.max(el, 0))) / 90;
  const pt = (az: number, el: number) => {
    const r = rOf(el);
    const a = (az * Math.PI) / 180;
    return [c + r * Math.sin(a), c - r * Math.cos(a)] as const;
  };

  const wedges = useMemo(() => {
    const step = horizon.azimuth_step_deg;
    return horizon.elevation_deg.map((el, i) => {
      if (el === null || el <= 0) return null;
      const a0 = i * step - step / 2;
      const a1 = i * step + step / 2;
      const [x0, y0] = pt(a0, 0);
      const [x1, y1] = pt(a1, 0);
      const [x2, y2] = pt(a1, el);
      const [x3, y3] = pt(a0, el);
      return (
        <path
          key={i}
          d={`M${x0},${y0} L${x1},${y1} L${x2},${y2} L${x3},${y3} Z`}
          fill={COLORS[horizon.source[i] ?? "none"]}
          opacity={0.85}
        />
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [horizon]);

  const canopy = horizon.canopy_elevation_deg
    .map((el, i) => (el === null ? null : pt(i * horizon.azimuth_step_deg, el)))
    .map((p, i) => (p ? <circle key={i} cx={p[0]} cy={p[1]} r={1.3} fill="var(--tree)" /> : null));

  const sunPath = useMemo(() => {
    const out: JSX.Element[] = [];
    const start = Date.parse(`${date}T00:00:00Z`) - 3 * 3600_000;
    for (let t = start; t < start + 30 * 3600_000; t += 5 * 60_000) {
      const d = new Date(t);
      const p = solarPosition(d, spot.lat, spot.lon);
      if (p.elevation <= 0) continue;
      const seg = segments.find((s) => Date.parse(s.start) <= t && t < Date.parse(s.end));
      if (!seg) continue;
      const [x, y] = pt(p.azimuth, p.elevation);
      out.push(<circle key={t} cx={x} cy={y} r={2.6} fill={segColor(seg)} stroke="#1d1d1b" strokeWidth={0.4} />);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date, spot, segments]);

  return (
    <svg viewBox={`0 0 ${S} ${S}`} width="100%" role="img" aria-label="Horizon profile and sun path">
      {[0, 30, 60].map((el) => (
        <circle key={el} cx={c} cy={c} r={rOf(el)} fill="none" stroke="var(--line)" />
      ))}
      {wedges}
      {canopy}
      {[0, 90, 180, 270].map((az) => {
        const [x, y] = pt(az, -6);
        return (
          <text key={az} x={x} y={y + 3} textAnchor="middle">
            {["N", "E", "S", "W"][az / 90]}
          </text>
        );
      })}
      {(() => {
        const [x, y] = pt(spot.facing_azimuth_deg, 45);
        return <line x1={c} y1={c} x2={x} y2={y} stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="4 3" />;
      })()}
      {sunPath}
    </svg>
  );
}
