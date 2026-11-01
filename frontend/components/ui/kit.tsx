import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-cond text-3xl font-semibold tracking-tight">{title}</h1>
      {children && <p className="max-w-[72ch] text-ink-2">{children}</p>}
    </header>
  );
}

export function Metrics({ items }: { items: [string, ReactNode, string?][] }) {
  return (
    <dl className="flex flex-wrap gap-x-10 gap-y-3">
      {items.map(([k, v, note]) => (
        <div key={k} title={note}>
          <dt className="text-xs text-muted">{k}</dt>
          <dd className="num text-2xl text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Panel({ title, children, className, aside }: { title?: string; children: ReactNode; className?: string; aside?: ReactNode }) {
  return (
    <section className={clsx("panel p-4", className)}>
      {(title || aside) && (
        <div className="mb-3 flex items-baseline justify-between gap-3">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button({ children, primary, className, ...rest }: { children: ReactNode; primary?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...rest} className={clsx("rounded px-3 py-1.5 text-sm font-medium disabled:opacity-60",
      primary ? "bg-ink text-bg" : "border border-line text-ink hover:bg-raised", className)}>
      {children}
    </button>
  );
}

export function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor: string }) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1 text-xs text-muted">
      {label}
      {children}
    </label>
  );
}

export const inputCls = "rounded border border-line bg-raised px-2 py-1.5 text-sm text-ink";

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="flex items-center gap-2 text-sm text-ink">
      <span aria-hidden className="h-2 w-2 rounded-full bg-critical" />{message}
    </p>
  );
}

export const fmtPct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
export const fmtSigned = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(d)}%`;
export const fmtUnits = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}u`);
