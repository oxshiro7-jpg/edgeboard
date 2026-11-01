"use client";
import { useState } from "react";
import type { RankedPick } from "@/lib/types";
import { call } from "@/lib/client";
import { useSlip } from "./SlipContext";

export function PickActions({ pick }: { pick: RankedPick }) {
  const slip = useSlip();
  const [tracked, setTracked] = useState<string | null>(null);
  const inSlip = slip.legs.some((l) => l.fixture_id === pick.fixture_id && l.market_key === pick.key);
  return (
    <>
      <button
        onClick={() => inSlip ? slip.remove(pick.fixture_id, pick.key)
          : slip.add({ fixture_id: pick.fixture_id, market_key: pick.key, label: pick.label, match: pick.match, odds: pick.bookmaker_odds })}
        className="rounded border border-line px-2.5 py-1 text-sm text-ink hover:bg-raised">
        {inSlip ? "Remove from multiple" : "Add to multiple"}
      </button>
      <button disabled={tracked !== null}
        onClick={async () => {
          try { await call("bets", { fixture_id: pick.fixture_id, market_key: pick.key }); setTracked("Tracked"); }
          catch (e) { setTracked((e as Error).message); }
        }}
        className="rounded border border-line px-2.5 py-1 text-sm text-ink hover:bg-raised disabled:text-muted">
        {tracked ?? "Track"}
      </button>
    </>
  );
}
