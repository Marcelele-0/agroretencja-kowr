import { useEffect, useMemo, useRef, useState } from "react";
import { backendEnabled } from "../api/backend";
import type { ZonesResponse } from "../api/contract";
import type { MapZone } from "../map/ParcelMap";
import { jointRequestOf, zonesByParcel, zonesCache, type JointZoneInput } from "./jointZones";

const DEBOUNCE_MS = 150;

export interface JointZones {
  /** Joint zones per parcel id (last resolved request; stays visible while a newer one runs). */
  zones: Record<string, MapZone[]>;
  /** A joint result for the current parcels and states is expected but not in yet. */
  loading: boolean;
}

/**
 * Zones of all listed parcels from one POST /api/zones on a shared grid, so neighbours line up at their borders.
 * Fires (debounced) as soon as any row has a base state; re-fires when a row joins or a rounded state changes.
 * Requests are shared through zonesCache and never aborted by re-renders; answers for an older key are cached but not shown.
 */
export function useJointZones(rows: JointZoneInput[], rev = 0): JointZones {
  const plan = jointRequestOf(rows);
  // `rev` grows when a parcel's NMT 1 m arrived: the same states then need zones from the new terrain.
  const key = backendEnabled && plan ? (rev ? `${plan.key}#${rev}` : plan.key) : "";
  const planRef = useRef(plan);
  planRef.current = plan;
  const latest = useRef(key);
  latest.current = key;
  const mounted = useRef(true);
  const [shown, setShown] = useState<{ key: string; data: ZonesResponse } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [, setOffTick] = useState(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const hasPending = rows.some((r) => r.pending);

  useEffect(() => {
    if (!key) return;
    const hit = zonesCache.peek(key);
    if (hit) {
      setShown({ key, data: hit });
      return;
    }
    if (!zonesCache.available()) return;
    const req = planRef.current!.req;
    const run = () => {
      zonesCache.get(key, req).then(
        (data) => { if (mounted.current && latest.current === key) setShown({ key, data }); },
        () => {
          if (!mounted.current) return;
          if (latest.current === key) setFailed(key);
          if (!zonesCache.available()) setOffTick((n) => n + 1);
        },
      );
    };
    if (!hasPending) {
      run();
      return;
    }
    // The debounce only delays initial arrivals while rows are still pending; a started request is never cancelled.
    const t = setTimeout(run, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [key, hasPending]);

  const zones = useMemo(() => zonesByParcel(shown?.data), [shown]);
  const expecting = backendEnabled && zonesCache.available();
  const loading = expecting && ((!!key && shown?.key !== key && failed !== key) || hasPending);
  return { zones, loading };
}
