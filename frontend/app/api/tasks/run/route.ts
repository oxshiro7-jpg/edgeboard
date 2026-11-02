// Public entry for the scheduler (cron-job.org). Protected by CRON_SECRET (checked by the backend),
// not by the site password, so the scheduler can reach it. Wakes the free host as a side effect.
const API = process.env.API_URL ?? "http://localhost:8000";

async function run(req: Request) {
  const token = new URL(req.url).searchParams.get("token") ?? "";
  const res = await fetch(`${API}/api/tasks/run?token=${encodeURIComponent(token)}`, { method: "POST", cache: "no-store" });
  return Response.json(await res.json().catch(() => ({ detail: "backend unavailable" })), { status: res.status });
}

export const dynamic = "force-dynamic";
export const maxDuration = 300;
export async function GET(req: Request) { return run(req); }
export async function POST(req: Request) { return run(req); }
