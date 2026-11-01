"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { MatchCard, RiskCategory } from "@/lib/types";
import { ago, kickoff, odds } from "@/lib/format";
import { ProbStrip } from "./ProbStrip";
import { Confidence, RiskBadge, StatusLabel, VerificationBadge } from "./ui/badges";

const RISK_RANK: Record<string, number> = { "Very Low": 0, Low: 1, Moderate: 2, High: 3, "Very High": 4 };
const SORTS = {
  kickoff: { label: "Kickoff", fn: (a: MatchCard, b: MatchCard) => (a.kickoff ?? "").localeCompare(b.kickoff ?? "") },
  confidence: { label: "Highest confidence", fn: (a: MatchCard, b: MatchCard) => (b.confidence ?? 0) - (a.confidence ?? 0) },
  ev: { label: "Highest expected value", fn: (a: MatchCard, b: MatchCard) => (b.value_score ?? 0) - (a.value_score ?? 0) },
  risk: { label: "Lowest risk", fn: (a: MatchCard, b: MatchCard) => (RISK_RANK[a.risk ?? ""] ?? 9) - (RISK_RANK[b.risk ?? ""] ?? 9) },
  edge: { label: "Highest model edge", fn: (a: MatchCard, b: MatchCard) => (b.max_edge_pp ?? -99) - (a.max_edge_pp ?? -99) },
  pick: { label: "Best single bet", fn: (a: MatchCard, b: MatchCard) => (b.best_pick_score ?? 0) - (a.best_pick_score ?? 0) },
} as const;

export function MatchTable({ cards }: { cards: MatchCard[] }) {
  const [comp, setComp] = useState("all");
  const [minConf, setMinConf] = useState(0);
  const [maxRisk, setMaxRisk] = useState<RiskCategory | "any">("any");
  const [onlyRecs, setOnlyRecs] = useState(false);
  const [onlyVerified, setOnlyVerified] = useState(false);
  const [sort, setSort] = useState<keyof typeof SORTS>("kickoff");

  const comps = useMemo(() => Array.from(new Map(cards.filter((c) => c.competition).map((c) => [c.competition!.id, c.competition!.name]))), [cards]);
  const rows = useMemo(() => cards
    .filter((c) => comp === "all" || c.competition?.id === comp)
    .filter((c) => minConf === 0 || (c.confidence ?? 0) >= minConf)
    .filter((c) => maxRisk === "any" || (c.risk != null && RISK_RANK[c.risk] <= RISK_RANK[maxRisk]))
    .filter((c) => !onlyRecs || (c.recommended_count ?? 0) > 0)
    .filter((c) => !onlyVerified || c.verification_status === "VERIFIED")
    .sort(SORTS[sort].fn), [cards, comp, minConf, maxRisk, onlyRecs, onlyVerified, sort]);

  return (
    <section aria-labelledby="matches-h" className="panel overflow-hidden">
      <div className="flex flex-wrap items-end gap-3 border-b border-line p-3">
        <h2 id="matches-h" className="mr-auto text-base font-semibold">Today&apos;s matches</h2>
        <Select label="Competition" value={comp} onChange={setComp}
          options={[["all", "All"], ...comps]} />
        <Select label="Min confidence" value={String(minConf)} onChange={(v) => setMinConf(Number(v))}
          options={[["0", "Any"], ["50", "50+"], ["60", "60+"], ["70", "70+"], ["80", "80+"]]} />
        <Select label="Max risk" value={maxRisk} onChange={(v) => setMaxRisk(v as RiskCategory | "any")}
          options={[["any", "Any"], ["Low", "Low"], ["Moderate", "Moderate"], ["High", "High"]]} />
        <Select label="Sort" value={sort} onChange={(v) => setSort(v as keyof typeof SORTS)}
          options={Object.entries(SORTS).map(([k, s]) => [k, s.label])} />
        <label className="flex items-center gap-2 pb-1.5 text-sm text-ink-2">
          <input type="checkbox" checked={onlyRecs} onChange={(e) => setOnlyRecs(e.target.checked)} className="accent-[rgb(var(--home))]" />
          With picks only
        </label>
        <label className="flex items-center gap-2 pb-1.5 text-sm text-ink-2">
          <input type="checkbox" checked={onlyVerified} onChange={(e) => setOnlyVerified(e.target.checked)} className="accent-[rgb(var(--home))]" />
          Verified only
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-line">
            <tr>
              <th className="th">Kickoff</th>
              <th className="th">Match</th>
              <th className="th">Verification</th>
              <th className="th min-w-[180px]">Model H / D / A</th>
              <th className="th text-right">Odds 1 / X / 2</th>
              <th className="th text-right">xG</th>
              <th className="th">Best market</th>
              <th className="th">Confidence</th>
              <th className="th">Risk</th>
              <th className="th">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.fixture_id} className="border-b border-line/60 last:border-0 hover:bg-raised/50">
                <td className="td num text-ink-2">{c.kickoff ? kickoff(c.kickoff) : "—"}</td>
                <td className="td">
                  {c.analysed ? (
                    <Link href={`/matches/${c.fixture_id}`} className="font-medium text-ink hover:underline">
                      {c.home?.name} <span className="text-muted">v</span> {c.away?.name}
                    </Link>
                  ) : (
                    <span className="font-medium text-ink-2">{c.home?.name ?? "?"} <span className="text-muted">v</span> {c.away?.name ?? "?"}</span>
                  )}
                  <div className="text-xs text-muted">
                    {c.competition?.name ?? "Unknown competition"}, fixture {c.fixture_id}{c.data_updated_at ? `, data ${ago(c.data_updated_at)}` : ""}
                  </div>
                </td>
                <td className="td"><VerificationBadge status={c.verification_status} title={c.verification_messages.join("\n")} /></td>
                {c.analysed && c.probabilities ? (
                  <>
                    <td className="td"><ProbStrip {...c.probabilities} market={c.market_probabilities ?? null} height={8} /></td>
                    <td className="td num text-right text-ink-2">
                      {c.odds?.H != null ? <>{odds(c.odds.H)} <span className="text-muted">/</span> {odds(c.odds.D)} <span className="text-muted">/</span> {odds(c.odds.A)}</> : "ODDS UNAVAILABLE"}
                    </td>
                    <td className="td num text-right">
                      {c.expected_goals!.home.toFixed(2)}–{c.expected_goals!.away.toFixed(2)}
                      <div className="text-xs text-muted">{c.expected_score_range}</div>
                    </td>
                    <td className="td">
                      {c.best_market ? (
                        <>
                          <div className="text-ink">{c.best_market}</div>
                          <div className="text-xs text-muted">{c.recommended_count} pick{c.recommended_count === 1 ? "" : "s"}, score {c.best_pick_score}</div>
                        </>
                      ) : (
                        <span className="text-muted" title={c.no_bet?.reasons.join("; ")}>No bet</span>
                      )}
                    </td>
                    <td className="td"><Confidence value={c.confidence ?? null} /></td>
                    <td className="td"><RiskBadge risk={c.risk ?? null} /></td>
                    <td className="td">{c.status ? <StatusLabel status={c.status} /> : null}</td>
                  </>
                ) : (
                  <td className="td whitespace-normal text-sm text-ink-2" colSpan={7}>
                    No prediction. {c.verification_messages[0] ?? ""}
                  </td>
                )}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={10} className="td py-8 text-center text-ink-2">No fixtures meet these filters. The list only ever contains fixtures returned by the fixtures API.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: (readonly [string, string])[];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="rounded border border-line bg-raised px-2 py-1.5 text-sm text-ink">
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
