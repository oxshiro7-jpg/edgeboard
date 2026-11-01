import Link from "next/link";

export default function NotFound() {
  return (
    <div className="p-10">
      <h1 className="text-2xl font-semibold">Match not found</h1>
      <p className="mt-2 text-ink-2">It may be outside today&apos;s slate or the competition whitelist.</p>
      <Link href="/" className="mt-4 inline-block text-home hover:underline">Back to dashboard</Link>
    </div>
  );
}
