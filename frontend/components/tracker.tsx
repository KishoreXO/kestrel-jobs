"use client";

import { useState } from "react";
import { inputCls } from "@/components/ui";
import { jobErrorMessage, post, STATUS_LABELS, type Job, type JobStatus } from "@/lib/api";

export function StatusSelect({ value, onChange }: { value: JobStatus; onChange: (s: JobStatus) => void }) {
  return (
    <select
      aria-label="Application status"
      className={`${inputCls} py-1.5`}
      value={value}
      onChange={(e) => onChange(e.target.value as JobStatus)}
    >
      {(Object.keys(STATUS_LABELS) as JobStatus[]).map((s) => (
        <option key={s} value={s}>
          {STATUS_LABELS[s]}
        </option>
      ))}
    </select>
  );
}

/** Thumbs up / down: remembered in your profile so future matching leans toward what you like. */
export function FeedbackButtons({
  job,
  onChanged,
  onError,
}: {
  job: Job;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [value, setValue] = useState(job.feedback);
  const send = async (v: number) => {
    const previous = value;
    const next = value === v ? 0 : v;
    setValue(next);
    try {
      await post(`/jobs/${job.id}/feedback`, { value: next });
    } catch (e) {
      setValue(previous);
      onError(jobErrorMessage(e));
    }
    onChanged();
  };
  const base = "rounded-lg border px-2 py-1.5 text-sm transition";
  const off = "border-ink-200 text-ink-500 hover:bg-ink-100 dark:border-ink-700 dark:hover:bg-ink-800";
  return (
    <div className="flex shrink-0 gap-1">
      <button
        title="More like this"
        aria-label="More like this"
        aria-pressed={value === 1}
        onClick={() => send(1)}
        className={`${base} ${value === 1 ? "border-emerald-500 bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : off}`}
      >
        👍
      </button>
      <button
        title="Less like this (archives it)"
        aria-label="Less like this (archives it)"
        aria-pressed={value === -1}
        onClick={() => send(-1)}
        className={`${base} ${value === -1 ? "border-rose-500 bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300" : off}`}
      >
        👎
      </button>
    </div>
  );
}
