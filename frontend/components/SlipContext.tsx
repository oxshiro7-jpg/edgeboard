"use client";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type SlipLeg = { fixture_id: string; market_key: string; label: string; match: string; odds: number | null };

type Slip = { legs: SlipLeg[]; add: (l: SlipLeg) => void; remove: (fid: string, key: string) => void; set: (l: SlipLeg[]) => void; clear: () => void };
const Ctx = createContext<Slip | null>(null);
const KEY = "eb_slip";

export function SlipProvider({ children }: { children: ReactNode }) {
  const [legs, setLegs] = useState<SlipLeg[]>([]);
  useEffect(() => {
    try { const raw = localStorage.getItem(KEY); if (raw) setLegs(JSON.parse(raw)); } catch { /* storage unavailable */ }
  }, []);
  const persist = (next: SlipLeg[]) => { setLegs(next); try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* ignore */ } };
  return (
    <Ctx.Provider value={{
      legs,
      add: (l) => { if (!legs.some((x) => x.fixture_id === l.fixture_id && x.market_key === l.market_key)) persist([...legs, l]); },
      remove: (fid, key) => persist(legs.filter((x) => !(x.fixture_id === fid && x.market_key === key))),
      set: persist,
      clear: () => persist([]),
    }}>{children}</Ctx.Provider>
  );
}

export function useSlip(): Slip {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSlip must be used inside SlipProvider");
  return s;
}
