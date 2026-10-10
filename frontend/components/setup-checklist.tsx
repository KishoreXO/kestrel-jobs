"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Card } from "@/components/ui";
import { useLoader } from "@/lib/hooks";
import { api } from "@/lib/api";

type Steps = Record<"contact_email" | "classifier" | "drafting_model" | "profile" | "sources", boolean>;

const STEPS: { key: keyof Steps; label: string; hint: string; href: string }[] = [
  { key: "contact_email", label: "Add your contact email", hint: "Sent in the User-Agent so sites know who is asking.", href: "/settings" },
  { key: "classifier", label: "Choose a classifier", hint: "Jev or another classification/decision model is recommended.", href: "/settings" },
  { key: "profile", label: "Fill in your profile", hint: "Upload a resume, then check target titles and skills.", href: "/profile" },
  { key: "sources", label: "Pick companies to watch", hint: "Start from the starter pack or paste careers URLs.", href: "/companies" },
  { key: "drafting_model", label: "Set a model for answer drafts", hint: "Optional, used by “Help me answer”.", href: "/settings" },
];

export function SetupChecklist() {
  const [steps, setSteps] = useState<Steps | null>(null);
  const load = useCallback(async () => {
    try {
      setSteps((await api<{ steps: Steps }>("/setup-status")).steps);
    } catch {
      /* backend errors are shown by the page */
    }
  }, []);
  useLoader(load);

  // Answer drafting is optional, so it doesn't keep the checklist open.
  if (!steps || STEPS.every((s) => steps[s.key] || s.key === "drafting_model")) return null;
  const done = STEPS.filter((s) => steps[s.key]).length;
  return (
    <Card className="space-y-3 border-accent-200 dark:border-accent-900">
      <div className="flex items-baseline justify-between">
        <h2 className="font-semibold">Get set up</h2>
        <span className="text-xs text-ink-500">
          {done} of {STEPS.length} done
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800">
        <div className="h-full bg-accent-500" style={{ width: `${(done / STEPS.length) * 100}%` }} />
      </div>
      <ol className="grid gap-2 sm:grid-cols-2">
        {STEPS.map((s) => (
          <li key={s.key}>
            <Link
              href={s.href}
              className="flex gap-2 rounded-lg p-2 hover:bg-ink-50 dark:hover:bg-ink-800/60"
            >
              <span
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ${
                  steps[s.key]
                    ? "bg-accent-600 text-white dark:bg-accent-500 dark:text-ink-950"
                    : "border border-ink-300 dark:border-ink-600"
                }`}
              >
                {steps[s.key] ? "✓" : ""}
              </span>
              <span>
                <span className={`block text-sm font-medium ${steps[s.key] ? "text-ink-400 line-through" : ""}`}>
                  {s.label}
                </span>
                <span className="block text-xs text-ink-500">{s.hint}</span>
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </Card>
  );
}
