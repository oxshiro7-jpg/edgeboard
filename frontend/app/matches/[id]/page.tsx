import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { ago, kickoff, odds, pct } from "@/lib/format";
import { ProbStrip } from "@/components/ProbStrip";
import { MarketTable } from "@/components/MarketTable";
import { ModelComponents, MovementPanel, Recommendation, ScoreGrid, TeamCompare } from "@/components/analysis";
import { StatusLabel, VerificationBadge } from "@/components/ui/badges";
import { PickActions } from "@/components/PickActions";
import type { Analysis, MatchEntry } from "@/lib/types";

export const dynamic = "force-dynamic";

const RANK_TITLES: [keyof Analysis["ranking"], string][] = [
  ["best_overall", "Best overall"], ["best_conservative", "Best conservative"], ["best_value", "Best value"],
  ["best_higher_odds", "Best higher odds"], ["best_goals", "Best goals bet"], ["best_result", "Best result bet"],
];

export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const entry = await api.match(id).catch(() => null);
  if (!entry) notFound();
  if (!entry.analysis) return <NoPrediction entry={entry} />;
  const a = entry.analysis;
  const upd = await api.lineupUpdate(id).catch(() => null);
  const matchName = `${a.home.name} vs ${a.away.name}`;
  const p = a.prediction.probabilities;
  const nv = Object.fromEntries(a.markets.filter((m) => m.key.startsWith("1X2:")).map((m) => [m.key, m.no_vig_probability]));
  const market = nv["1X2:H"] && nv["1X2:D"] && nv["1X2:A"] ? { home: nv["1X2:H"]!, draw: nv["1X2:D"]!, away: nv["1X2:A"]! } : null;
  const seen = new Set<string>();
  const ranked = RANK_TITLES.flatMap(([k, title]) => {
    const m = a.ranking[k];
    if (!m || Array.isArray(m) || seen.has(m.key)) return [];
    seen.add(m.key);
    return [{ title, m }];
  });

  return (
    <div>
      <header className="border-b border-line px-6 py-5">
        <Link href="/" className="text-sm text-ink-2 hover:text-ink">Back to dashboard</Link>
        <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-sm text-ink-2">{a.competition.name}, kickoff {kickoff(a.kickoff)}</p>
            <h1 className="font-cond text-3xl font-semibold tracking-tight">
              <span className="text-home">■</span> {a.home.name} <span className="text-muted">v</span> {a.away.name} <span className="text-away">■</span>
            </h1>
          </div>
          <div className="text-right text-sm text-ink-2">
            <div className="flex justify-end gap-2"><VerificationBadge status={entry.status} /> <StatusLabel status={a.summary.status} /></div>
            <div className="mt-1 text-xs text-muted">Fixture {a.provenance.fixture_id} from {a.provenance.provider}, verified {ago(a.provenance.verified_at)}</div>
            <div className="mt-1">Data quality <span className="num text-ink">{a.data_quality.score.toFixed(0)}</span>/100, updated {ago(a.data_updated_at)}</div>
            <div className="text-xs text-muted">{a.model.version}, input {a.model.input_hash}</div>
          </div>
        </div>
        <div className="mt-5 max-w-3xl">
          <div className="mb-1 flex justify-between text-sm">
            <span className="text-ink">{a.home.short_name} {pct(p.home)}</span>
            <span className="text-ink-2">Draw {pct(p.draw)}</span>
            <span className="text-ink">{pct(p.away)} {a.away.short_name}</span>
          </div>
          <ProbStrip {...p} market={market} height={14} showLabels={false} />
          <p className="mt-2 text-xs text-muted">
            Bars: model. Ticks: bookmaker prices with margin removed
            {market ? ` (${pct(market.home)} / ${pct(market.draw)} / ${pct(market.away)})` : " (not available)"}.
          </p>
        </div>
      </header>

      <div className="grid gap-6 p-6">
        {a.no_bet.no_bet ? (
          <section className="panel border-line p-5">
            <h2 className="text-lg font-semibold">No bet</h2>
            <p className="mt-1 text-ink-2">Nothing in this match clears the thresholds. Main reasons:</p>
            <ul className="mt-2 list-disc pl-5 text-ink-2">{a.no_bet.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          </section>
        ) : (
          <section aria-label="Recommended bets" className="grid gap-4 xl:grid-cols-2">
            {ranked.map(({ title, m }) => (
              <Recommendation key={m.key} title={title} m={m} actions={
                <PickActions pick={{ ...m, fixture_id: a.fixture_id, match: matchName, competition: a.competition.name, kickoff: a.kickoff, home: a.home.name, away: a.away.name }} />
              } />
            ))}
          </section>
        )}

        {upd?.updated && upd.before && upd.after && (
          <section className="panel p-4" aria-labelledby="upd-h">
            <h2 id="upd-h" className="text-base font-semibold">Prediction updated after confirmed lineups</h2>
            <p className="num mt-1 text-ink">
              Before lineup: {a.home.short_name} {pct(upd.before.home)}, draw {pct(upd.before.draw)}, {a.away.short_name} {pct(upd.before.away)}.
              After lineup: {pct(upd.after.home)}, {pct(upd.after.draw)}, {pct(upd.after.away)}.
            </p>
            <ul className="mt-2 list-disc pl-5 text-sm text-ink-2">{upd.reasons?.map((r) => <li key={r}>{r}</li>)}</ul>
            {upd.before_best !== upd.after_best && <p className="mt-2 text-sm text-ink-2">Best bet changed from {upd.before_best ?? "no bet"} to {upd.after_best ?? "no bet"}.</p>}
          </section>
        )}

        {a.ranking.avoided.length > 0 && (
          <section className="panel p-4" aria-labelledby="av-h">
            <h2 id="av-h" className="text-base font-semibold">Passed on</h2>
            <ul className="mt-2 divide-y divide-line/60 text-sm">
              {a.ranking.avoided.map((m) => (
                <li key={m.key} className="flex flex-wrap justify-between gap-2 py-2">
                  <span className="text-ink">{m.label} <span className="num text-ink-2">{pct(m.model_probability)} at {odds(m.bookmaker_odds)}</span></span>
                  <span className="text-ink-2">{m.reasons_rejected.join("; ")}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="grid gap-6 xl:grid-cols-2">
          <TeamCompare a={a} />
          <div className="flex flex-col gap-6">
            <ModelComponents a={a} />
            <ScoreGrid a={a} />
          </div>
        </div>

        <div className="grid gap-6 xl:grid-cols-3">
          <Notes title="Squad" items={[
            `${a.home.name} lineup ${a.home.lineup.status}${a.home.lineup.formation ? ` (${a.home.lineup.formation})` : ""}`,
            ...a.home.absence_impact.lines.map((l) => `${a.home.short_name}: ${l}`),
            `${a.away.name} lineup ${a.away.lineup.status}${a.away.lineup.formation ? ` (${a.away.lineup.formation})` : ""}`,
            ...a.away.absence_impact.lines.map((l) => `${a.away.short_name}: ${l}`),
          ]} />
          <Notes title="Context and form signals" items={[...a.context.notes, ...a.flags.map((f) => f.text)]}
            empty="No unusual context or misleading-form signals." />
          <Notes title="Tactical matchup" items={a.tactics.map((t) => t.text)}
            empty="No standout style mismatches in the available data." />
        </div>

        <MarketTable markets={a.markets} />

        <div className="grid gap-6 xl:grid-cols-2">
          <MovementPanel moves={a.movement} />
          <section className="panel p-4" aria-labelledby="dq-h">
            <h2 id="dq-h" className="text-base font-semibold">Data quality {a.data_quality.score.toFixed(0)}/100</h2>
            <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
              {Object.entries(a.data_quality.factors).map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-line/40 py-1">
                  <dt className="capitalize text-ink-2">{k.replaceAll("_", " ")}</dt><dd className="num">{v.toFixed(0)}</dd>
                </div>
              ))}
            </dl>
            {a.data_quality.gaps.length > 0 && (
              <ul className="mt-3 list-disc pl-4 text-sm text-ink-2">{a.data_quality.gaps.map((g) => <li key={g}>{g}</li>)}</ul>
            )}
            <h3 className="mt-4 text-sm font-medium">Head to head</h3>
            <p className="text-xs text-muted">{a.h2h.note}</p>
            <ul className="mt-1 text-sm text-ink-2">
              {a.h2h.matches.length ? a.h2h.matches.map((m) => <li key={m.date} className="num">{m.date} ({m.venue}) {m.score}{m.xg ? `, xG ${m.xg}` : ""}</li>)
                : <li>No recent meetings.</li>}
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

function Notes({ title, items, empty }: { title: string; items: string[]; empty?: string }) {
  return (
    <section className="panel p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-ink-2">
        {items.length ? items.map((t) => <li key={t}>{t}</li>) : <li className="list-none -ml-4">{empty}</li>}
      </ul>
    </section>
  );
}

function NoPrediction({ entry }: { entry: MatchEntry }) {
  const l = entry.verification.listed;
  return (
    <div className="flex max-w-3xl flex-col gap-4 p-6">
      <Link href="/" className="text-sm text-ink-2 hover:text-ink">Back to dashboard</Link>
      <h1 className="font-cond text-3xl font-semibold tracking-tight">
        {l ? `${l.home.name ?? "?"} v ${l.away.name ?? "?"}` : `Fixture ${entry.fixture_id}`}
      </h1>
      <div><VerificationBadge status={entry.status} /></div>
      <section className="panel p-4">
        <h2 className="text-base font-semibold">No prediction</h2>
        <ul className="mt-2 list-disc pl-5 text-ink-2">{entry.messages.map((m) => <li key={m}>{m}</li>)}</ul>
        <p className="mt-3 text-sm text-muted">Predictions are only produced for fixtures the fixtures API returns and the verification checks pass. Missing data is never filled in.</p>
      </section>
    </div>
  );
}
