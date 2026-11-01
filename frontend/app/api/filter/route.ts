import { NextResponse } from "next/server";

// Proxies the pick filter to the backend so API_URL stays server-side.
export async function POST(req: Request) {
  const { query } = (await req.json()) as { query?: string };
  if (!query || query.length > 500) return NextResponse.json({ detail: "Enter a filter of up to 500 characters" }, { status: 422 });
  const res = await fetch(`${process.env.API_URL ?? "http://localhost:8000"}/api/picks/filter`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    cache: "no-store",
  });
  return NextResponse.json(await res.json(), { status: res.status });
}
