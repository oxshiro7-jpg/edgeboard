"use client";
import { useEffect, useState } from "react";
import { call } from "@/lib/client";
import type { CalTable } from "@/lib/lab-types";
import { CalibrationChart } from "@/components/charts";
import { ErrorNote, PageHeader, Panel } from "@/components/ui/kit";

interface Models {
  active_version: string; settled_matches: number; calibration_source?: string;
  component_status?: Record<string, { active: boolean; weight: number; reason?: string }> | null;
  config: { ensemble: Record<string, number>; thresholds: Record<string, number | string> };
  result_forecast: { model: { log_loss: number; brier: number }; market: { log_loss: number; brier: number } };
  components: Record<string, { n: number; log_loss: number; brier: number }>;
  calibration: CalTable;
  calibration_by_family: Record<string, { n: number; ece: number | null; degraded: boolean; message: string }>;
  suggested_calibration_factors: Record<string, number>;
  drift: string | null;
  windows: { baseline: { log_loss: number; n: number }; recent: { log_loss: number; n: number } };
  ml: null | { status: string; message: string; recommended_weight: number; test: Record<string, { log_loss?: number }>; split: Record<string, number> };
  gbm_available: string | null;
}

export default function ModelsPage() {
  const [m, setM] = useState<Models | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { call<Models>("models").then(setM).catch((e) => setErr(e.message)); }, []);
  if (!m) return <div className="p-6"><ErrorNote message={err} /><p className="text-ink-2">Evaluating models.</p></div>;
  return (
    <div className="flex flex-col gap-6 p-6">
      <PageHeader title="Models">
        Active version {m.active_version}, evaluated on {m.settled_matches} settled matches. Historical predictions are
        stored with their version and inputs and are never overwritten.
      </PageHeader>
      {m.drift && <p className="panel border-critical/50 p-3 text-sm text-ink">{m.drift}</p>}
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="Calibration">
          <p className="mb-2 text-sm text-muted">Source: {m.calibration_source ?? "backtest: every priced selection"}.</p>
          <CalibrationChart rows={m.calibration.rows} />
          <p className="mt-2 text-sm text-ink-2">{m.calibration.message} {m.calibration.ece !== null ? ` Expected calibration error ${m.calibration.ece}.` : ""}</p>
          <table className="mt-3 w-full text-sm">
            <thead><tr className="border-b border-line"><th className="th pl-0">Bucket</th><th className="th text-right">n</th><th className="th text-right">Predicted</th><th className="th text-right">Actual</th><th className="th">Flag</th></tr></thead>
            <tbody>
              {m.calibration.rows.map((r) => (
                <tr key={r.bucket} className="border-b border-line/60">
                  <td className="py-1.5">{r.bucket}</td><td className="num text-right">{r.n}</td>
                  <td className="num text-right">{r.predicted !== null ? (r.predicted * 100).toFixed(1) + "%" : "—"}</td>
                  <td className="num text-right">{r.actual !== null ? (r.actual * 100).toFixed(1) + "%" : "—"}</td>
                  <td className="text-ink-2">{r.flag ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
        <div className="flex flex-col gap-6">
          <Panel title="Component accuracy (1X2 log loss, lower is better)">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line"><th className="th pl-0">Component</th><th className="th text-right">Weight</th><th className="th text-right">Log loss</th><th className="th text-right">Brier</th></tr></thead>
              <tbody>
                {Object.entries(m.components).map(([k, v]) => {
                  const st = m.component_status?.[k];
                  return (
                    <tr key={k} className="border-b border-line/60">
                      <td className="py-1.5 capitalize">
                        {k.replace("_", " ")}
                        {st && !st.active && <div className="text-xs normal-case text-muted">{st.reason}</div>}
                      </td>
                      <td className="num text-right text-ink-2">
                        {st ? (st.active ? `${(st.weight * 100).toFixed(0)}%` : "off") : `${((m.config.ensemble[k] ?? 0) * 100).toFixed(0)}%`}
                      </td>
                      <td className="num text-right">{v.n ? v.log_loss : "—"}</td><td className="num text-right">{v.n ? v.brier : "—"}</td>
                    </tr>
                  );
                })}
                <tr className="font-medium"><td className="py-1.5">Ensemble</td><td /><td className="num text-right">{m.result_forecast.model.log_loss}</td><td className="num text-right">{m.result_forecast.model.brier}</td></tr>
              </tbody>
            </table>
            <p className="mt-2 text-sm text-ink-2">
              Windows: first half log loss {m.windows.baseline.log_loss} (n={m.windows.baseline.n}), recent half {m.windows.recent.log_loss} (n={m.windows.recent.n}).
            </p>
          </Panel>
          <Panel title="Calibration by market">
            <ul className="flex flex-col gap-1 text-sm">
              {Object.entries(m.calibration_by_family).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-3">
                  <span className="capitalize">{k.replace("_", " ")} <span className="text-muted">(n={v.n})</span></span>
                  <span className="num text-ink-2">ECE {v.ece ?? "—"}{m.suggested_calibration_factors[k] ? `, confidence factor ${m.suggested_calibration_factors[k]}` : ""}{v.degraded ? ", flagged" : ""}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Machine-learning challenger">
            {m.ml ? (
              <div className="text-sm">
                <p className="text-ink">{m.ml.message}</p>
                <p className="mt-1 text-ink-2">Test log loss: ensemble {m.ml.test.ensemble?.log_loss}, ensemble plus ML {m.ml.test.ensemble_plus_ml?.log_loss}, market {m.ml.test.market?.log_loss}.
                  Split {m.ml.split.train}/{m.ml.split.validation}/{m.ml.split.test} matches.</p>
              </div>
            ) : <p className="text-sm text-ink-2">Not trained yet. An admin trains it with POST /api/admin/ml/train; it joins the ensemble only if it beats it on an untouched test window.</p>}
            <p className="mt-2 text-xs text-muted">Gradient boosting library available: {m.gbm_available ?? "none installed"}.</p>
          </Panel>
        </div>
      </div>
    </div>
  );
}
