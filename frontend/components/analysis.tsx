import clsx from "clsx";
import type { ReactNode } from "react";
import type { Analysis, Market, Movement, TeamBlock } from "@/lib/types";
import { odds, pct, pp } from "@/lib/format";
import { Confidence, EV, RiskBadge, TagChip } from "./ui/badges";

/* ------------------------------------------------------------ comparison */

const RATING_ROWS: [keyof TeamBlock["ratings"], string][] = [
  ["attack", "Attack"], ["defence", "Defence"], ["form", "Form (opponent-weighted)"],
  ["venue_strength", "Home / away strength"], ["squad_availability", "Squad availability"], ["model_rating", "Model rating"],
];

export function TeamCompare({ a }: { a: Analysis }) {
  const h = a.home, w = a.away;
  const fmt = (v: number | null | undefined, d = 2) => (v === null || v === undefined ? "—" : v.toFixed(d));
  const statRows: [string, string, string][] = [
    ["xG per game", fmt(h.last10?.xg_for), fmt(w.last10?.xg_for)],
    ["xGA per game", fmt(h.last10?.xg_against), fmt(w.last10?.xg_against)],
    ["xG difference", fmt(h.last10?.xgd), fmt(w.last10?.xgd)],
    ["Goals minus xG (last 10)", fmt(h.last10?.goals_minus_xg, 1), fmt(w.last10?.goals_minus_xg, 1)],
    ["Shots / on target", `${fmt(h.last10?.shots, 1)} / ${fmt(h.last10?.shots_on_target, 1)}`, `${fmt(w.last10?.shots, 1)} / ${fmt(w.last10?.shots_on_target, 1)}`],
    ["Big chances for / against", `${fmt(h.last10?.big_chances, 1)} / ${fmt(h.last10?.big_chances_conceded, 1)}`, `${fmt(w.last10?.big_chances, 1)} / ${fmt(w.last10?.big_chances_conceded, 1)}`],
    ["Points (xPts), last 5", `${h.last5?.points ?? "—"} (${fmt(h.last5?.expected_points, 1)})`, `${w.last5?.points ?? "—"} (${fmt(w.last5?.expected_points, 1)})`],
    ["Form", h.last5?.form_string ?? "—", w.last5?.form_string ?? "—"],
    ["Elo", String(h.elo), String(w.elo)],
  ];
  return (
    <section className="panel p-4" aria-labelledby="cmp-h">
      <h2 id="cmp-h" className="mb-3 text-base font-semibold">Team comparison</h2>
      <div className="mb-2 grid grid-cols-[1fr_auto_1fr] text-sm font-medium">
        <span className="text-ink">{h.name}</span><span /><span className="text-right text-ink">{w.name}</span>
      </div>
      <ul className="flex flex-col gap-2.5">
        {RATING_ROWS.map(([k, label]) => {
          const hv = h.ratings[k], av = w.ratings[k];
          return (
            <li key={k} className="grid grid-cols-[1fr_9rem_1fr] items-center gap-3 text-sm">
              <Bar value={hv} side="home" />
              <span className="text-center text-xs text-ink-2">{label}</span>
              <Bar value={av} side="away" />
            </li>
          );
        })}
      </ul>
      <table className="mt-4 w-full text-sm">
        <tbody>
          {statRows.map(([k, hv, av]) => (
            <tr key={k} className="border-t border-line/60">
              <td className="num py-1.5 text-ink">{hv}</td>
              <td className="py-1.5 text-center text-xs text-muted">{k}</td>
              <td className="num py-1.5 text-right text-ink">{av}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Bar({ value, side }: { value: number | null; side: "home" | "away" }) {
  if (value === null) return <span className={clsx("text-xs text-muted", side === "away" && "text-right")}>No data</span>;
  return (
    <div className={clsx("flex items-center gap-2", side === "home" ? "flex-row-reverse" : "")}>
      <div className="h-2 flex-1 rounded-sm bg-line/50">
        <div className={clsx("h-2 rounded-sm", side === "home" ? "ml-auto bg-home" : "bg-away")} style={{ width: `${value}%` }} />
      </div>
      <span className="num w-7 text-right text-ink">{value.toFixed(0)}</span>
    </div>
  );
}

/* ------------------------------------------------------------ score grid */

export function ScoreGrid({ a }: { a: Analysis }) {
  const m = a.prediction.score_matrix;
  const max = Math.max(...m.flat());
  return (
    <section className="panel p-4" aria-labelledby="sg-h">
      <h2 id="sg-h" className="text-base font-semibold">Score distribution</h2>
      <p className="mt-1 text-sm text-ink-2">
        Expected goals {a.prediction.expected_goals.home.toFixed(2)} – {a.prediction.expected_goals.away.toFixed(2)},
        total {a.prediction.expected_goals.total.toFixed(2)}. Rows are {a.home.short_name} goals, columns {a.away.short_name} goals.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="text-xs" aria-label="Probability of each exact score">
          <thead>
            <tr><th />{m[0].map((_, j) => <th key={j} className="num px-1 pb-1 font-normal text-muted">{j}</th>)}</tr>
          </thead>
          <tbody>
            {m.map((row, i) => (
              <tr key={i}>
                <th className="num pr-2 font-normal text-muted">{i}</th>
                {row.map((p, j) => (
                  <td key={j} className="p-[1px]">
                    <div title={`${i}-${j}: ${pct(p, 1)}`}
                      className="num flex h-9 w-12 items-center justify-center rounded-[3px] text-ink"
                      style={{ background: `color-mix(in oklab, rgb(var(--home)) ${Math.round((p / max) * 85)}%, rgb(var(--panel)))` }}>
                      {p >= 0.005 ? (p * 100).toFixed(1) : ""}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ol className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {a.prediction.score_distribution.slice(0, 6).map((s) => (
          <li key={s.score} className="num text-ink-2"><span className="text-ink">{s.score}</span> {pct(s.p, 1)}</li>
        ))}
      </ol>
    </section>
  );
}

/* ------------------------------------------------------------ model components */

export function ModelComponents({ a }: { a: Analysis }) {
  const p = a.prediction;
  return (
    <section className="panel p-4" aria-labelledby="mc-h">
      <h2 id="mc-h" className="text-base font-semibold">Ensemble</h2>
      <p className="mt-1 text-sm text-ink-2">
        Models disagree by {(p.model_disagreement * 100).toFixed(0)} pp on the home win
        {p.model_disagreement > 0.12 ? ", above the threshold, so result markets are blocked." : "."}
      </p>
      <table className="mt-3 w-full text-sm">
        <thead><tr className="border-b border-line">
          <th className="th pl-0">Component</th><th className="th text-right">Weight</th>
          <th className="th text-right">Home</th><th className="th text-right">Draw</th><th className="th text-right">Away</th>
          <th className="th text-right">xG</th>
        </tr></thead>
        <tbody>
          {p.components.map((c) => (
            <tr key={c.name} className="border-b border-line/60" title={c.note}>
              <td className="py-1.5 capitalize text-ink">{c.name.replace("_", " ")}</td>
              <td className="num text-right text-ink-2">{pct(c.weight)}</td>
              <td className="num text-right">{pct(c.home)}</td>
              <td className="num text-right">{pct(c.draw)}</td>
              <td className="num text-right">{pct(c.away)}</td>
              <td className="num text-right text-ink-2">{c.lambdas ? `${c.lambdas[0].toFixed(2)}–${c.lambdas[1].toFixed(2)}` : "—"}</td>
            </tr>
          ))}
          <tr className="font-medium">
            <td className="py-1.5 text-ink">Ensemble</td><td />
            <td className="num text-right">{pct(p.probabilities.home)}</td>
            <td className="num text-right">{pct(p.probabilities.draw)}</td>
            <td className="num text-right">{pct(p.probabilities.away)}</td>
            <td className="num text-right">{p.expected_goals.home.toFixed(2)}–{p.expected_goals.away.toFixed(2)}</td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

/* ------------------------------------------------------------ recommendation */

export function Recommendation({ title, m, actions }: { title: string; m: Market; actions?: ReactNode }) {
  return (
    <article className="panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted">{title}</p>
          <h3 className="text-lg font-semibold leading-snug">{m.label}</h3>
        </div>
        <TagChip tag={m.label_tag} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-3 text-sm sm:grid-cols-6">
        <KV k="Odds" v={<span className="num text-ink">{odds(m.bookmaker_odds)}</span>} />
        <KV k="Fair odds" v={<span className="num text-ink-2">{odds(m.fair_odds)}</span>} />
        <KV k="Probability" v={<span className="num text-ink">{pct(m.model_probability)}</span>} />
        <KV k="EV" v={<EV ev={m.ev} />} />
        <KV k="Confidence" v={<Confidence value={m.confidence} compact />} />
        <KV k="Risk" v={<RiskBadge risk={m.risk} />} />
      </dl>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="text-sm font-medium text-ink">Why this bet</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-sm text-ink-2">{m.why?.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
        <div>
          <h4 className="text-sm font-medium text-ink">Risks</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-sm text-ink-2">
            {(m.risks?.length ? m.risks : ["No specific risk flags beyond normal match variance."]).map((r) => <li key={r}>{r}</li>)}
          </ul>
          <h4 className="mt-3 text-sm font-medium text-ink">Value</h4>
          <p className="mt-1 text-sm text-ink-2">
            Fair {odds(m.fair_odds)} against {odds(m.bookmaker_odds)} offered; market (no-vig) {pct(m.no_vig_probability)},
            edge {pp(m.edge_pp)}. Pick score {m.pick_score}.
          </p>
        </div>
      </div>
      {actions && <div className="mt-4 flex gap-2 border-t border-line pt-3">{actions}</div>}
    </article>
  );
}

function KV({ k, v }: { k: string; v: ReactNode }) {
  return <div><dt className="text-xs text-muted">{k}</dt><dd className="mt-0.5">{v}</dd></div>;
}

/* ------------------------------------------------------------ movement */

export function MovementPanel({ moves }: { moves: Movement[] }) {
  const top = moves.slice(0, 6);
  return (
    <section className="panel p-4" aria-labelledby="mv-h">
      <h2 id="mv-h" className="text-base font-semibold">Market movement</h2>
      <p className="mt-1 text-sm text-ink-2">
        Largest shifts in implied probability since opening. Moves can reflect news or large stakes; they are not treated as
        informed money by default.
      </p>
      <ul className="mt-3 divide-y divide-line/60">
        {top.map((m) => (
          <li key={m.market_key} className="flex items-center gap-3 py-2 text-sm">
            <span className="w-32 truncate text-ink" title={m.market_key}>{m.market_key}</span>
            <Spark points={[m.opening, ...m.history.map((h) => h.odds), m.current]} />
            <span className="num text-ink-2">{odds(m.opening)} to {odds(m.current)}</span>
            <span className="num ml-auto text-ink">{pp(m.implied_shift_pp)}</span>
            {m.flags.length > 0 && <span className="chip">{m.flags.join(", ")}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Spark({ points }: { points: number[] }) {
  const w = 90, hgt = 22;
  const lo = Math.min(...points), hi = Math.max(...points);
  const span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? "L" : "M"}${(i / (points.length - 1)) * w},${hgt - 2 - ((p - lo) / span) * (hgt - 4)}`).join(" ");
  return (
    <svg width={w} height={hgt} aria-hidden className="shrink-0">
      <path d={d} fill="none" stroke="rgb(var(--ink-2))" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
