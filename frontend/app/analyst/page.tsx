"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import { useSlip } from "@/components/SlipContext";
import { Button, ErrorNote, PageHeader } from "@/components/ui/kit";

type Msg = { role: "you" | "analyst"; text: string; tools?: { tool: string }[] };
const EXAMPLES = ["Which match has the strongest statistical edge today?", "Build me a conservative 3-leg multiple.",
  "Give me bets between 1.40 and 1.80.", "Find 5 picks with confidence above 70.", "Remove the riskiest selection."];

export default function AnalystPage() {
  const slip = useSlip();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function ask(question: string) {
    if (!question.trim()) return;
    setBusy(true); setErr(null); setQ("");
    setMsgs((m) => [...m, { role: "you", text: question }]);
    try {
      const r = await call<{ answer: string; tool_calls: { tool: string }[]; slip?: { fixture_id: string; market_key: string; label: string }[] }>("analyst", {
        question, slip: slip.legs.map(({ fixture_id, market_key }) => ({ fixture_id, market_key })),
      });
      setMsgs((m) => [...m, { role: "analyst", text: r.answer, tools: r.tool_calls }]);
      if (r.slip) slip.set(slip.legs.filter((l) => r.slip!.some((x) => x.fixture_id === l.fixture_id && x.market_key === l.market_key)));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="flex max-w-3xl flex-col gap-5 p-6">
      <PageHeader title="AI analyst">
        Answers come only from today&apos;s stored analysis and your current multiple. If the data does not contain something, the analyst says so.
      </PageHeader>
      <div className="flex flex-wrap gap-2">{EXAMPLES.map((e) => <button key={e} className="chip hover:text-ink" onClick={() => ask(e)}>{e}</button>)}</div>
      <ol className="flex flex-col gap-3" aria-live="polite">
        {msgs.map((m, i) => (
          <li key={i} className={m.role === "you" ? "self-end rounded-lg bg-raised px-3 py-2" : "panel p-3"}>
            <p className="whitespace-pre-line text-sm text-ink">{m.text}</p>
            {m.tools && m.tools.length > 0 && <p className="mt-2 text-xs text-muted">Data used: {m.tools.map((t) => t.tool.replaceAll("_", " ")).join(", ")}</p>}
          </li>
        ))}
      </ol>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input aria-label="Ask the analyst" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Why is Valemont to win not recommended?"
          className="min-w-0 flex-1 rounded border border-line bg-raised px-3 py-2 text-ink" />
        <Button primary disabled={busy}>{busy ? "Thinking" : "Ask"}</Button>
      </form>
      <ErrorNote message={err} />
    </div>
  );
}
