import "server-only";
import type { Board, MatchCard, MatchEntry, RankedPick } from "./types";

// Server-side only. The browser never calls providers directly and never sees keys.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function get<T>(path: string, revalidate = 60): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { next: { revalidate } });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `API ${path} failed with ${res.status}`);   // e.g. NO VERIFIED FIXTURE: provider not configured
  }
  return res.json() as Promise<T>;
}

const q = (params: Record<string, string | number | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  board: (d?: string) => get<Board>(`/api/board${q({ d })}`),
  matches: (p: { d?: string; competition?: string; sort?: string; min_confidence?: string; max_risk?: string }) =>
    get<MatchCard[]>(`/api/matches${q(p)}`),
  match: (id: string, d?: string) => get<MatchEntry>(`/api/matches/${encodeURIComponent(id)}${q({ d })}`, 30),
  lineupUpdate: (id: string) => get<{
    fixture_id: string; updated: boolean; reason?: string; before?: { home: number; draw: number; away: number };
    after?: { home: number; draw: number; away: number }; before_best?: string | null; after_best?: string | null;
    reasons?: string[]; alerts?: { type: string; text: string }[];
  }>(`/api/matches/${encodeURIComponent(id)}/lineup-update`, 30),
  competitions: () => get<{ id: string; name: string; type: string; enabled: boolean }[]>("/api/competitions", 3600),
  filter: async (query: string, date?: string) => {
    const res = await fetch(`${API_URL}/api/picks/filter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, date }),
      cache: "no-store",
    });
    const body = await res.json();
    if (!res.ok) return { error: (body.detail as string) ?? "Filter failed", picks: [] as RankedPick[], count: 0 };
    return body as { query: string; count: number; picks: RankedPick[]; error?: undefined };
  },
};
