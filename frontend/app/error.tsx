"use client";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="p-10">
      <h1 className="text-2xl font-semibold">No verified data to show</h1>
      <p className="mt-2 max-w-prose text-ink-2">{error.message}</p>
      <p className="mt-2 max-w-prose text-sm text-muted">
        Nothing is displayed unless it comes from the connected fixtures and odds APIs. Check that the backend is running,
        API_URL points at it, and API_FOOTBALL_KEY is set on the backend.
      </p>
      <button onClick={reset} className="mt-4 rounded border border-line px-3 py-1.5 text-ink hover:bg-raised">Try again</button>
    </div>
  );
}
