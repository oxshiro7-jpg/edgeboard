"use client";

export async function call<T>(path: string, body?: unknown, method?: "GET" | "POST" | "PUT"): Promise<T> {
  const res = await fetch(`/api/proxy/${path}`, {
    method: method ?? (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed");
  return data as T;
}
