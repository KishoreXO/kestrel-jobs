"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLoader } from "@/lib/hooks";
import { Alert, Button, Card, Meter, TierBadge, inputCls } from "@/components/ui";
import { Attribution } from "@/components/attribution";
import { SetupChecklist } from "@/components/setup-checklist";
import { FeedbackButtons, StatusSelect } from "@/components/tracker";
import {
  api,
  jobErrorMessage,
  matchedSkills,
  patch,
  post,
  safeUrl,
  STATUS_LABELS, type Job, type JobStatus, type Tier } from "@/lib/api";

const TIERS: { key: Tier; label: string }[] = [
  { key: "strong", label: "Strong" },
  { key: "good", label: "Good" },
  { key: "pending", label: "Pending" },
  { key: "rejected", label: "Filtered out" },
];

// "" = every job you haven't archived, sorted into match tiers.
const VIEWS: { key: "" | JobStatus; label: string }[] = [
  { key: "", label: "Matches" },
  { key: "saved", label: "Saved" },
  { key: "applied", label: "Applied" },
  { key: "interviewing", label: "Interviewing" },
  { key: "offer", label: "Offer" },
  { key: "rejected", label: STATUS_LABELS.rejected },
  { key: "archived", label: "Archived" },
];

const LAST_VISIT_KEY = "kestrel-last-visit";
// Scan log lines worth showing without opening the log.
const PROBLEM = /not ready|failed|stopped|empty|couldn't|rejected|paused/i;

interface ScanStatus {
  running: boolean;
  last: { status: string; fetched: number; new: number; classified: number; log: string[]; finished_at: string | null } | null;
}

interface JobsResponse {
  jobs: Job[];
  counts: Record<string, number>;
  status_counts: Record<string, number>;
}

// Read once per page load, so re-running effects (React dev mode, remounts) can't overwrite it first.
let previousVisit: number | null | undefined;

function readPreviousVisit(): number | null {
  if (previousVisit === undefined) {
    previousVisit = null;
    try {
      const raw = localStorage.getItem(LAST_VISIT_KEY);
      previousVisit = raw ? Number(raw) : null;
      localStorage.setItem(LAST_VISIT_KEY, String(Date.now()));
    } catch {
      /* storage unavailable: no badges */
    }
  }
  return previousVisit;
}

/** When you last opened this page (kept in this browser only). Jobs first seen after it get a "New" badge. */
function useLastVisit(): number | null {
  const [since, setSince] = useState<number | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read browser storage after hydration
    setSince(readPreviousVisit());
  }, []);
  return since;
}

export default function JobsPage() {
  const [view, setView] = useState<"" | JobStatus>("");
  const [tier, setTier] = useState<Tier>("strong");
  const [q, setQ] = useState("");
  const [newOnly, setNewOnly] = useState(false);
  const [data, setData] = useState<JobsResponse>({ jobs: [], counts: {}, status_counts: {} });
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showLog, setShowLog] = useState(false);
  const [askApplied, setAskApplied] = useState<Job | null>(null);
  const opened = useRef<Job | null>(null);
  const latestRequest = useRef(0);
  const since = useLastVisit();

  const load = useCallback(async () => {
    // Typing in the filter starts a request per key: only the newest answer may update the list.
    const request = ++latestRequest.current;
    try {
      const tierParam = view === "" ? tier : "";
      const result = await api<JobsResponse>(`/jobs?status=${view}&tier=${tierParam}&q=${encodeURIComponent(q)}`);
      if (request !== latestRequest.current) return;
      setData(result);
      setError("");
    } catch (e) {
      if (request === latestRequest.current) setError(`Can't reach the Kestrel backend: ${(e as Error).message}`);
    }
  }, [view, tier, q]);

  const loadScan = useCallback(async () => {
    try {
      setScan(await api<ScanStatus>("/scan/status"));
    } catch {
      /* shown via load() */
    }
  }, []);

  useLoader(load);
  useLoader(loadScan);

  useEffect(() => {
    if (!scan?.running) return;
    const t = setInterval(async () => {
      await loadScan();
      await load();
    }, 3000);
    return () => clearInterval(t);
  }, [scan?.running, load, loadScan]);

  // After you open a posting and come back, offer to mark it as applied (never acts on the site).
  useEffect(() => {
    const onFocus = () => {
      if (opened.current) {
        setAskApplied(opened.current);
        opened.current = null;
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const startScan = async (path: "/scan" | "/classify" = "/scan") => {
    try {
      await post(path);
      setScan((s) => ({ running: true, last: s?.last ?? null }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const setStatus = async (id: number, status: JobStatus) => {
    try {
      await patch(`/jobs/${id}`, { status });
    } catch (e) {
      setNotice(`${jobErrorMessage(e)} The list was refreshed.`);
    }
    await load();
  };

  const isNew = (job: Job) => since !== null && !!job.first_seen && Date.parse(job.first_seen) > since;
  const jobs = newOnly ? data.jobs.filter(isNew) : data.jobs;
  const total = Object.values(data.status_counts).reduce((a, b) => a + b, 0);
  const pending = view === "" ? (data.counts.pending ?? 0) : 0;
  const problems = (scan?.last?.log ?? []).filter((l) => PROBLEM.test(l));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Your jobs</h1>
          <p className="text-sm text-ink-600 dark:text-ink-400">
            Only roles that pass every hard requirement and reach an 85% match score at 85% confidence or more. You
            always apply on the employer&apos;s site.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {scan?.last && !scan.running && (
            <button className="text-xs text-ink-500 underline" onClick={() => setShowLog((v) => !v)}>
              Last scan: {scan.last.status} · {scan.last.new} new · {scan.last.classified} classified
            </button>
          )}
          <a href="/api/jobs/export.csv" download className="text-xs text-ink-500 underline" title="Every job you saved, applied to or moved further">
            Export CSV
          </a>
          <Button onClick={() => startScan()} disabled={scan?.running}>
            {scan?.running ? "Scanning…" : "Scan now"}
          </Button>
        </div>
      </div>

      <SetupChecklist />

      {showLog && scan?.last && (
        <Card className="text-xs">
          <ul className="space-y-1 font-mono">
            {scan.last.log.length ? scan.last.log.map((l, i) => <li key={i}>{l}</li>) : <li>No messages.</li>}
          </ul>
        </Card>
      )}

      {error && <Alert tone="error">{error}</Alert>}
      {notice && (
        <Alert tone="warn">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span>{notice}</span>
            <Button variant="ghost" className="px-2.5 py-1 text-xs" onClick={() => setNotice("")}>
              Dismiss
            </Button>
          </div>
        </Alert>
      )}

      {!scan?.running && problems.length > 0 && (
        <Alert tone="warn">
          <p className="font-medium">The last scan needs your attention:</p>
          <ul className="mt-1 list-disc pl-5">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Alert>
      )}

      {!scan?.running && pending > 0 && (
        <Alert tone="info">
          <div className="flex flex-wrap items-center gap-3">
            <span>
              {pending} job{pending === 1 ? " is" : "s are"} waiting to be classified.
            </span>
            <Button className="px-2.5 py-1 text-xs" onClick={() => startScan("/classify")}>
              Retry classification
            </Button>
          </div>
        </Alert>
      )}

      {askApplied && (
        <Alert tone="info">
          <div className="flex flex-wrap items-center gap-3">
            <span>
              Did you apply to <strong>{askApplied.title}</strong> at {askApplied.company}?
            </span>
            <Button
              className="px-2.5 py-1 text-xs"
              onClick={async () => {
                await setStatus(askApplied.id, "applied");
                setAskApplied(null);
              }}
            >
              Mark as applied
            </Button>
            <Button variant="ghost" className="px-2.5 py-1 text-xs" onClick={() => setAskApplied(null)}>
              Not yet
            </Button>
          </div>
        </Alert>
      )}

      <div className="flex gap-1 overflow-x-auto overflow-y-hidden border-b border-ink-200 dark:border-ink-800">
        {VIEWS.map((v) => {
          const count = v.key === "" ? total - (data.status_counts.archived ?? 0) : (data.status_counts[v.key] ?? 0);
          return (
            <button
              key={v.key || "matches"}
              onClick={() => setView(v.key)}
              className={`-mb-px shrink-0 border-b-2 px-3 py-2 text-sm font-medium ${
                view === v.key
                  ? "border-accent-600 text-accent-700 dark:border-accent-400 dark:text-accent-300"
                  : "border-transparent text-ink-500 hover:text-ink-800 dark:hover:text-ink-200"
              }`}
            >
              {v.label} <span className="opacity-60">{count}</span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {view === "" &&
          TIERS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTier(t.key)}
              className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition ${
                tier === t.key
                  ? "bg-ink-900 text-white dark:bg-accent-500 dark:text-ink-950"
                  : "bg-ink-100 text-ink-700 hover:bg-ink-200 dark:bg-ink-800 dark:text-ink-300"
              }`}
            >
              {t.label} <span className="opacity-60">{data.counts[t.key] ?? 0}</span>
            </button>
          ))}
        <label className="flex items-center gap-1.5 text-sm text-ink-600 dark:text-ink-400">
          <input type="checkbox" checked={newOnly} onChange={(e) => setNewOnly(e.target.checked)} />
          New since last visit
        </label>
        <input
          className={`${inputCls} sm:ml-auto sm:w-72`}
          placeholder="Filter by title, company, location"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      {jobs.length === 0 && !error && (
        <Card className="text-center text-sm text-ink-600 dark:text-ink-400">
          {total === 0 ? (
            <>
              No jobs yet. Finish the setup steps above, then click <strong>Scan now</strong>.
            </>
          ) : view === "" ? (
            "Nothing in this tier."
          ) : (
            `No jobs marked “${VIEWS.find((v) => v.key === view)?.label}”.`
          )}
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4">
        {jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            isNew={isNew(job)}
            onOpen={() => {
              if (job.status === "new" || job.status === "saved") opened.current = job;
            }}
            onChanged={load}
            onError={setNotice}
            onStatus={(s) => setStatus(job.id, s)}
          />
        ))}
      </div>
    </div>
  );
}

function JobCard({
  job,
  isNew,
  onOpen,
  onChanged,
  onError,
  onStatus,
}: {
  job: Job;
  isNew: boolean;
  onOpen: () => void;
  onChanged: () => void;
  onError: (message: string) => void;
  onStatus: (s: JobStatus) => void;
}) {
  const c = job.classification;
  const skills = matchedSkills(c);
  const postingUrl = safeUrl(job.url);
  return (
    <Card className="flex flex-col gap-3 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <TierBadge tier={c?.tier ?? "pending"} />
          {isNew && (
            <span className="rounded-full bg-accent-600 px-2 py-0.5 text-xs font-semibold text-white dark:bg-accent-500 dark:text-ink-950">
              New
            </span>
          )}
          <Link href={`/jobs/${job.id}`} className="min-w-0 truncate text-lg font-semibold hover:text-accent-600">
            {job.title}
          </Link>
        </div>
        <p className="text-sm text-ink-700 dark:text-ink-300">
          {job.company}
          {job.location && ` · ${job.location}`}
          {job.remote && " · Remote"}
          {job.salary && ` · ${job.salary}`}
        </p>
        {c?.reject_reason && <p className="text-xs text-ink-500">Filtered: {c.reject_reason}</p>}
        {c && c.ai_prohibited >= 0.5 && (
          <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
            ⚠ This employer asks applicants not to use AI tools.
          </p>
        )}
        {skills.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {skills.slice(0, 8).map((s) => (
              <span
                key={s}
                className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
              >
                ✓ {s}
              </span>
            ))}
          </div>
        )}
        <p className="line-clamp-2 text-sm text-ink-500 dark:text-ink-400">{job.snippet}</p>
        <Attribution job={job} />
      </div>
      <div className="flex w-full shrink-0 flex-col gap-2 sm:w-56">
        {c && c.tier !== "rejected" && <Meter value={c.match_score} label="Match score" />}
        {postingUrl && (
          <a href={postingUrl} target="_blank" rel="noopener noreferrer" onClick={onOpen}>
            <Button className="w-full">Open original posting ↗</Button>
          </a>
        )}
        <div className="flex items-center gap-2">
          <StatusSelect value={job.status} onChange={onStatus} />
          <FeedbackButtons job={job} onChanged={onChanged} onError={onError} />
        </div>
      </div>
    </Card>
  );
}
