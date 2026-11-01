"use client";
import clsx from "clsx";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV: { href: string; label: string; mobile?: boolean }[] = [
  { mobile: true, href: "/", label: "Dashboard" },
  { href: "/matches", label: "Matches" },
  { mobile: true, href: "/picks", label: "Best picks" },
  { mobile: true, href: "/multiples", label: "Multiples" },
  { href: "/slip", label: "Bet slip analyser" },
  { mobile: true, href: "/challenges", label: "Challenges" },
  { mobile: true, href: "/performance", label: "Performance" },
  { href: "/backtesting", label: "Backtesting" },
  { href: "/models", label: "Models" },
  { mobile: true, href: "/analyst", label: "AI analyst" },
  { href: "/settings", label: "Settings" },
];

export function Sidebar({ modelVersion }: { modelVersion: string }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-1 p-3">
      <Link href="/" className="mb-4 flex items-baseline gap-2 px-2 pt-1">
        <span className="font-cond text-xl font-semibold tracking-tight text-ink">EdgeBoard</span>
      </Link>
      {NAV.map((n) => {
        const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
        return (
          <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
            className={clsx("rounded px-2 py-1.5 text-sm",
              active ? "bg-raised text-ink" : "text-ink-2 hover:bg-raised/60 hover:text-ink")}>
            {n.label}
          </Link>
        );
      })}
      <div className="mt-auto px-2 pt-6 text-xs text-muted">Model {modelVersion}</div>
    </nav>
  );
}

/** Mobile: the sections the spec prioritises, as a scrollable top bar. */
export function MobileNav() {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="sticky top-0 z-10 flex gap-1 overflow-x-auto border-b border-line bg-panel px-3 py-2 md:hidden">
      <span className="mr-2 self-center font-cond text-lg font-semibold">EdgeBoard</span>
      {NAV.filter((n) => n.mobile).map((n) => {
        const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
        return (
          <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
            className={clsx("whitespace-nowrap rounded px-2 py-1 text-sm", active ? "bg-raised text-ink" : "text-ink-2")}>
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}
