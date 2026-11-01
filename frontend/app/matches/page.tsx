import { api } from "@/lib/api";
import { MatchTable } from "@/components/MatchTable";

export const dynamic = "force-dynamic";

export default async function MatchesPage() {
  const cards = await api.matches({});
  return (
    <div>
      <div className="p-6">
        <h1 className="mb-4 font-cond text-3xl font-semibold tracking-tight">Matches</h1>
        <MatchTable cards={cards} />
      </div>
    </div>
  );
}
