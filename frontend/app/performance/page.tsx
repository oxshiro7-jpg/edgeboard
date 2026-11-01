import { PerformanceView } from "@/components/PerformanceView";
import { PageHeader } from "@/components/ui/kit";

export const dynamic = "force-dynamic";

export default function PerformancePage() {
  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Performance">
        Judged on expected value, closing line value and calibration over many picks, not on win rate. Short runs are noise.
      </PageHeader>
      <PerformanceView />
    </div>
  );
}
