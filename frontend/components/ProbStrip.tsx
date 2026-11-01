import { pct } from "@/lib/format";

/**
 * Home / draw / away as one strip: model probabilities fill the bar, and thin
 * ticks mark where the de-margined market puts the home and away boundaries.
 * The gap between fill edge and tick is the model's disagreement with the price.
 */
export function ProbStrip({
  home, draw, away, market, height = 10, showLabels = true,
}: {
  home: number; draw: number; away: number;
  market?: { home: number; draw: number; away: number } | null;
  height?: number; showLabels?: boolean;
}) {
  const label = `Model: home ${pct(home)}, draw ${pct(draw)}, away ${pct(away)}` +
    (market ? `. Market: home ${pct(market.home)}, draw ${pct(market.draw)}, away ${pct(market.away)}` : "");
  return (
    <div className="w-full" role="img" aria-label={label}>
      <div className="relative flex w-full gap-[2px]" style={{ height }}>
        <div className="rounded-l-[4px] bg-home" style={{ width: `${home * 100}%` }} />
        <div className="bg-draw" style={{ width: `${draw * 100}%` }} />
        <div className="rounded-r-[4px] bg-away" style={{ width: `${away * 100}%` }} />
        {market && (
          <>
            <span className="absolute -top-[3px] w-[2px] bg-ink" style={{ left: `${market.home * 100}%`, height: height + 6 }} />
            <span className="absolute -top-[3px] w-[2px] bg-ink" style={{ left: `${(market.home + market.draw) * 100}%`, height: height + 6 }} />
          </>
        )}
      </div>
      {showLabels && (
        <div className="mt-1 flex justify-between text-xs text-ink-2 num">
          <span>{pct(home)}</span>
          <span>{pct(draw)}</span>
          <span>{pct(away)}</span>
        </div>
      )}
    </div>
  );
}
