import { api } from "@/lib/api";
import { SlipAnalyser } from "@/components/SlipAnalyser";
import { PageHeader } from "@/components/ui/kit";

export const dynamic = "force-dynamic";

export default async function SlipPage() {
  const cards = await api.matches({});
  // Only verified, analysed fixtures can be put on a slip.
  const matches = cards.filter((c) => c.analysed).map((c) => ({ id: c.fixture_id, label: `${c.home?.name} vs ${c.away?.name}` }));
  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Bet slip analyser">
        Enter the selections and the odds you were offered. Each leg is checked against the model, the slip is priced with
        correlation, and the engine proposes removals or replacements.
      </PageHeader>
      <SlipAnalyser matches={matches} />
    </div>
  );
}
