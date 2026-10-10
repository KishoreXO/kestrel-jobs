"use client";

import { useState } from "react";
import type { Tier } from "@/lib/api";

export function Button({
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  const styles = {
    primary:
      "bg-accent-600 text-white hover:bg-accent-700 disabled:bg-accent-300 dark:bg-accent-500 dark:text-ink-950 dark:hover:bg-accent-400 dark:disabled:bg-accent-900 dark:disabled:text-ink-400",
    secondary:
      "bg-white text-ink-800 border border-ink-300 hover:bg-ink-50 dark:bg-ink-800 dark:text-ink-100 dark:border-ink-600 dark:hover:bg-ink-700",
    ghost: "text-ink-600 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800",
    danger: "bg-rose-600 text-white hover:bg-rose-500",
  }[variant];
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    />
  );
}

export function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`rounded-xl border border-ink-200 bg-white p-5 shadow-sm dark:border-ink-700 dark:bg-ink-900 ${className}`}
      {...props}
    />
  );
}

const tierStyles: Record<Tier, string> = {
  strong: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200",
  good: "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200",
  maybe: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  rejected: "bg-ink-200 text-ink-700 dark:bg-ink-700 dark:text-ink-200",
  pending: "bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-200",
};

export function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold capitalize ${tierStyles[tier]}`}>{tier}</span>
  );
}

export function Meter({ value, label }: { value: number; label?: string }) {
  const v = Math.max(0, Math.min(1, value));
  const color = v >= 0.8 ? "bg-emerald-500" : v >= 0.65 ? "bg-sky-500" : v >= 0.5 ? "bg-amber-500" : "bg-ink-400";
  return (
    <div className="flex items-center gap-2" title={label}>
      <div className="h-2 w-full overflow-hidden rounded-full bg-ink-200 dark:bg-ink-700">
        <div className={`h-full ${color}`} style={{ width: `${v * 100}%` }} />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-ink-600 dark:text-ink-300">
        {Math.round(v * 100)}%
      </span>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium text-ink-800 dark:text-ink-200">{label}</span>
      {children}
      {hint && <span className="block text-xs text-ink-500 dark:text-ink-400">{hint}</span>}
    </label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900 outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-200 dark:border-ink-600 dark:bg-ink-800 dark:text-ink-100 dark:focus:ring-accent-900";

export function Alert({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "error" | "success";
  children: React.ReactNode;
}) {
  const styles = {
    info: "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-100",
    warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100",
    error: "border-rose-300 bg-rose-50 text-rose-900 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100",
    success:
      "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100",
  }[tone];
  return <div className={`rounded-lg border px-4 py-3 text-sm ${styles}`}>{children}</div>;
}

export function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  return (
    <Button
      variant="secondary"
      className="px-2.5 py-1 text-xs"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setState("copied");
        } catch {
          setState("failed"); // clipboard blocked by the browser: the text can still be selected by hand
        }
        setTimeout(() => setState("idle"), 1500);
      }}
    >
      {state === "copied" ? "Copied ✓" : state === "failed" ? "Select and copy" : "Copy"}
    </Button>
  );
}

export function ListInput({
  value,
  onChange,
  placeholder,
  rows = 3,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  rows?: number;
}) {
  const [text, setText] = useState(value.join("\n"));
  return (
    <textarea
      className={inputCls}
      rows={rows}
      placeholder={placeholder}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(
          e.target.value
            .split(/\n|,(?![^(]*\))/)
            .map((s) => s.trim())
            .filter(Boolean),
        );
      }}
    />
  );
}
