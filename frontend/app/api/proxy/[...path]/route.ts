import { NextResponse } from "next/server";

// Forwards browser calls to the backend so API_URL and any provider keys stay server-side.
const ALLOWED = ["multiples/", "slip/", "bets", "bankroll", "challenges", "performance", "backtest", "models", "analyst", "matches/"];
const API = process.env.API_URL ?? "http://localhost:8000";

async function forward(req: Request, path: string[]) {
  const p = path.join("/");
  if (!ALLOWED.some((a) => p === a.replace(/\/$/, "") || p.startsWith(a))) {
    return NextResponse.json({ detail: "Not found" }, { status: 404 });
  }
  const url = new URL(req.url);
  const init: RequestInit = { method: req.method, headers: { "Content-Type": "application/json" }, cache: "no-store" };
  if (req.method !== "GET") init.body = await req.text();
  const res = await fetch(`${API}/api/${p}${url.search}`, init);
  return NextResponse.json(await res.json(), { status: res.status });
}

type Ctx = { params: Promise<{ path: string[] }> };
export async function GET(req: Request, ctx: Ctx) { return forward(req, (await ctx.params).path); }
export async function POST(req: Request, ctx: Ctx) { return forward(req, (await ctx.params).path); }
export async function PUT(req: Request, ctx: Ctx) { return forward(req, (await ctx.params).path); }
