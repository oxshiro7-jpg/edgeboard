// Backend status for the site owner (behind the site password): storage, scheduler, API allowance.
const API = process.env.API_URL ?? "http://localhost:8000";

export const dynamic = "force-dynamic";
export async function GET() {
  const res = await fetch(`${API}/api/health`, { cache: "no-store" }).catch(() => null);
  if (!res) return Response.json({ detail: "backend unavailable" }, { status: 502 });
  return Response.json(await res.json(), { status: res.status });
}
