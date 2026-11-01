"use client";
import Link from "next/link";
import { useSlip } from "./SlipContext";

export function HeroSlip() {
  const { legs } = useSlip();
  const odds = legs.reduce((acc, l) => acc * (l.odds ?? 1), 1);
  return (
    <div>
      <dt className="text-xs text-muted">Active multiple</dt>
      <dd className="num text-2xl text-ink">
        {legs.length ? <Link href="/multiples" className="hover:underline">{legs.length} legs, {odds.toFixed(2)}</Link> : "—"}
      </dd>
    </div>
  );
}
