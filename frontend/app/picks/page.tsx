import { api } from "@/lib/api";
import { FilterConsole } from "@/components/FilterConsole";
import { PickCard } from "@/components/PickCard";
import type { RankedPick } from "@/lib/types";

export const dynamic = "force-dynamic";

const SECTIONS: [keyof Awaited<ReturnType<typeof api.board>>["board"], string, string][] = [
  ["highest_confidence", "Highest confidence", "Where inputs, models and calibration agree most."],
  ["best_value", "Best value", "Largest expected value among selections that pass every gate."],
  ["conservative", "Conservative", "High probability with low or very low risk."],
  ["high_risk_value", "Higher-risk value", "Positive expected value with wider variance. Size stakes accordingly."],
];

export default async function PicksPage() {
  const [board, initial] = await Promise.all([api.board(), api.filter("Status = recommended")]);
  return (
    <div>
      <div className="flex flex-col gap-8 p-6">
        <header>
          <h1 className="font-cond text-3xl font-semibold tracking-tight">Best picks</h1>
          <p className="mt-1 max-w-[70ch] text-ink-2">
            Probability, confidence, value and risk are scored separately. A selection appears here only if its price
            beats the model&apos;s fair odds by the minimum margin and every quality gate passes.
          </p>
        </header>
        {SECTIONS.map(([key, title, blurb]) => (
          <Section key={key} title={title} blurb={blurb} picks={board.board[key]} />
        ))}
        <section aria-labelledby="flt-h">
          <h2 id="flt-h" className="mb-3 text-lg font-semibold">Filter engine</h2>
          <FilterConsole initial={initial.picks} />
        </section>
      </div>
    </div>
  );
}

function Section({ title, blurb, picks }: { title: string; blurb: string; picks: RankedPick[] }) {
  return (
    <section>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mb-3 text-sm text-ink-2">{blurb}</p>
      {picks.length ? (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {picks.slice(0, 6).map((p) => <PickCard key={`${p.fixture_id}${p.key}`} pick={p} />)}
        </div>
      ) : (
        <p className="text-sm text-muted">Nothing qualifies today.</p>
      )}
    </section>
  );
}
