import Link from "next/link";
import { api } from "@/lib/api";
import { odds, pct, TIME_ZONE } from "@/lib/format";
import { MatchTable } from "@/components/MatchTable";
import { PickCard } from "@/components/PickCard";
import { HeroSlip } from "@/components/HeroSlip";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [board, cards] = await Promise.all([api.board(), api.matches({})]);
  const h = board.hero;
  const s = board.summary;
  const metrics: [string, string, string?][] = [
    ["Fixtures from API", String(h.todays_games)],
    ["Verified", String(h.verified_games)],
    ["Recommended picks", String(h.recommended_picks)],
    ["Average confidence", h.average_confidence?.toFixed(0) ?? "—"],
    ["Average EV", h.average_ev_pct !== null ? `+${h.average_ev_pct.toFixed(1)}%` : "—"],
    ["Current streak", h.current_streak ?? "—", h.record_source],
    ["Monthly ROI", h.monthly_roi !== null ? `${h.monthly_roi >= 0 ? "+" : "−"}${Math.abs(h.monthly_roi * 100).toFixed(1)}%` : "—", h.record_source],
  ];

  return (
    <div>
      <header className="border-b border-line px-6 pb-4 pt-6">
        <h1 className="font-cond text-3xl font-semibold tracking-tight">
          {new Date(`${board.date}T12:00:00Z`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", timeZone: TIME_ZONE })}
        </h1>
        <p className="mt-1 max-w-[70ch] text-ink-2">
          {board.verification.fixtures_from_provider} fixtures returned by the fixtures API in supported competitions
          ({Object.entries(board.verification.by_status).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}). Only VERIFIED
          fixtures are analysed for recommendations: {s.matches_with_recommendations} have a price worth taking,
          {" "}{s.rejected_selections} priced selections were rejected.
        </p>
        <dl className="mt-5 flex flex-wrap gap-x-10 gap-y-3">
          {metrics.map(([k, v, note]) => (
            <div key={k} title={note}>
              <dt className="text-xs text-muted">{k}</dt>
              <dd className="num text-2xl text-ink">{v}</dd>
            </div>
          ))}
          <HeroSlip />
        </dl>
      </header>

      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-6">
          <MatchTable cards={cards} />
          {s.best_edge && (
            <section className="panel p-4" aria-labelledby="edge-h">
              <h2 id="edge-h" className="text-base font-semibold">Largest model edge today</h2>
              <p className="mt-1 text-ink-2">
                {s.best_edge.match}: {s.best_edge.selection}. Model {pct(s.best_edge.model)} against a de-margined market
                of {pct(s.best_edge.market)} ({s.best_edge.edge_pp >= 0 ? "+" : ""}{s.best_edge.edge_pp.toFixed(1)} pp).
                Large gaps can also mean the model is missing news, so check team news before acting.
              </p>
            </section>
          )}
        </div>

        <aside className="flex flex-col gap-6">
          <section aria-labelledby="best-h">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 id="best-h" className="text-base font-semibold">Best picks today</h2>
              <Link href="/picks" className="text-sm text-ink-2 hover:text-ink">All picks and filters</Link>
            </div>
            <div className="flex flex-col gap-3">
              {board.board.best_picks.slice(0, 4).map((p) => <PickCard key={`${p.fixture_id}${p.key}`} pick={p} />)}
              {board.board.best_picks.length === 0 && (
                <p className="panel p-4 text-ink-2">No selection clears the thresholds today. That is a valid outcome: no bet.</p>
              )}
            </div>
          </section>

          <section className="panel p-4" aria-labelledby="avoid-h">
            <h2 id="avoid-h" className="text-base font-semibold">Likely, but not worth the price</h2>
            <p className="mt-1 text-sm text-ink-2">High-probability selections the engine passed on.</p>
            <ul className="mt-3 divide-y divide-line/60">
              {board.board.avoid.slice(0, 6).map((p) => (
                <li key={`${p.fixture_id}${p.key}`} className="py-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <span className="text-ink">{p.label}</span>
                    <span className="num text-ink-2">{pct(p.model_probability)} at {odds(p.bookmaker_odds)}</span>
                  </div>
                  <div className="text-xs text-muted">{p.match}. {p.reasons_rejected[0]}</div>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
