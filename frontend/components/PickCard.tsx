import Link from "next/link";
import type { ReactNode } from "react";
import type { RankedPick } from "@/lib/types";
import { kickoff, odds, pct } from "@/lib/format";
import { Confidence, EV, RiskBadge, TagChip } from "./ui/badges";
import { PickActions } from "./PickActions";

export function PickCard({ pick }: { pick: RankedPick }) {
  return (
    <article className="panel flex flex-col gap-3 p-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-[15px] font-semibold leading-snug text-ink">{pick.label}</h3>
          <p className="mt-0.5 text-sm text-ink-2">
            {pick.match} <span className="text-muted">{pick.competition}, {kickoff(pick.kickoff)}</span>
          </p>
        </div>
        <TagChip tag={pick.label_tag} />
      </header>

      <dl className="grid grid-cols-3 gap-x-4 gap-y-2 text-sm">
        <Stat k="Odds" v={<span className="num text-lg text-ink">{odds(pick.bookmaker_odds)}</span>} />
        <Stat k="Fair odds" v={<span className="num text-lg text-ink-2">{odds(pick.fair_odds)}</span>} />
        <Stat k="Probability" v={<span className="num text-lg text-ink">{pct(pick.model_probability)}</span>} />
        <Stat k="EV" v={<EV ev={pick.ev} />} />
        <Stat k="Confidence" v={<Confidence value={pick.confidence} />} />
        <Stat k="Risk" v={<RiskBadge risk={pick.risk} />} />
      </dl>

      <footer className="flex items-center justify-between border-t border-line pt-3">
        <span className="text-sm text-ink-2">
          Pick score <span className="num text-base text-ink">{pick.pick_score ?? "—"}</span>
        </span>
        <div className="flex gap-2">
          <Link href={`/matches/${pick.fixture_id}`} className="rounded border border-line px-2.5 py-1 text-sm text-ink hover:bg-raised">
            Analyse
          </Link>
          <PickActions pick={pick} />
        </div>
      </footer>
    </article>
  );
}

function Stat({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted">{k}</dt>
      <dd className="mt-0.5">{v}</dd>
    </div>
  );
}
