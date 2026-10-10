"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";
import { useLoader } from "@/lib/hooks";
import { Attribution } from "@/components/attribution";
import { Alert, Button, Card, CopyButton, Meter, TierBadge, inputCls } from "@/components/ui";
import { FeedbackButtons, StatusSelect } from "@/components/tracker";
import { ApiError, api, errorMessage, jobErrorMessage, patch, pct, post, put, safeUrl, type Draft, type FormQuestion, type Job, type JobStatus } from "@/lib/api";

type JobDetail = Job & { drafts: Draft[]; questions_available: boolean };

const GATE_LABELS: Record<string, string> = {
  experience_ok: "Experience requirement",
  qualifications_ok: "Required qualifications",
  location_ok: "Location / remote",
  work_auth_ok: "Work authorization",
  legit: "Genuine posting",
};
const SCORE_LABELS: Record<string, string> = {
  title_fit: "Role fit",
  skills_fit: "Skills fit",
  seniority_fit: "Seniority fit",
  domain_fit: "Domain fit",
  preference_fit: "Your preferences",
};

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState("");
  const [gone, setGone] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setJob(await api<JobDetail>(`/jobs/${id}`));
      setError("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setGone(true);
      else setError(errorMessage(e));
    }
  }, [id]);

  useLoader(load);

  if (gone) {
    return (
      <Card className="space-y-3 text-center">
        <p className="font-medium">This job no longer exists.</p>
        <p className="text-sm text-ink-500">It may have been removed, or your data was reset.</p>
        <Link href="/jobs" className="text-sm text-accent-600 hover:underline">
          ← Back to jobs
        </Link>
      </Card>
    );
  }
  if (error && !job) return <Alert tone="error">{error}</Alert>;
  if (!job) return <p className="text-sm text-ink-500">Loading…</p>;
  const c = job.classification;
  const aiProhibited = (c?.ai_prohibited ?? 0) >= 0.5;
  const postingUrl = safeUrl(job.url);
  const applyUrl = safeUrl(job.apply_url);
  const report = (message: string) => setNotice(message);

  return (
    <div className="space-y-6">
      <Link href="/jobs" className="text-sm text-accent-600 hover:underline">
        ← Back to jobs
      </Link>

      {(notice || error) && <Alert tone="warn">{notice || error}</Alert>}

      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <TierBadge tier={c?.tier ?? "pending"} />
          <h1 className="text-2xl font-semibold">{job.title}</h1>
        </div>
        <p className="text-ink-700 dark:text-ink-300">
          {job.company}
          {job.location && ` · ${job.location}`}
          {job.remote && " · Remote"}
          {job.salary && ` · ${job.salary}`}
        </p>
        <Attribution job={job} />
        <div className="flex flex-wrap gap-2 pt-1">
          {postingUrl && (
            <a href={postingUrl} target="_blank" rel="noopener noreferrer">
              <Button>Open original posting ↗</Button>
            </a>
          )}
          {applyUrl && applyUrl !== postingUrl && (
            <a href={applyUrl} target="_blank" rel="noopener noreferrer">
              <Button variant="secondary">Apply on employer site ↗</Button>
            </a>
          )}
          <Button
            variant="ghost"
            onClick={async () => {
              try {
                await post(`/jobs/${job.id}/reclassify`);
                setNotice("");
              } catch (e) {
                report(jobErrorMessage(e));
              }
              await load();
            }}
          >
            Re-check match
          </Button>
        </div>
      </Card>

      {c && c.backend !== "prefilter" && Object.keys(c.details || {}).length > 0 && (
        <Card className="space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">Why this matched</h2>
            <span className="text-xs text-ink-500">
              {c.backend === "jev" ? "Jev" : "Your model"} · {c.model} · confidence {pct(c.confidence)}
            </span>
          </div>
          {c.reject_reason && <Alert tone="warn">{c.reject_reason}</Alert>}
          <div className="grid gap-6 sm:grid-cols-2">
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-ink-500">Hard requirements</h3>
              {Object.entries(GATE_LABELS).map(([k, label]) => {
                const v = c.details[k]?.value ?? 0;
                return (
                  <div key={k} className="flex items-center justify-between text-sm">
                    <span>{label}</span>
                    <span className={v >= 0.5 ? "text-emerald-600" : "text-rose-600"}>
                      {v >= 0.5 ? "✓ met" : "✗ not met"} <span className="text-xs text-ink-400">({pct(v)})</span>
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-ink-500">Fit</h3>
              {Object.entries(SCORE_LABELS).map(([k, label]) => (
                <div key={k} className="grid grid-cols-[8rem_1fr] items-center gap-2 text-sm">
                  <span>{label}</span>
                  <Meter value={c.details[k]?.value ?? 0} />
                </div>
              ))}
              {c.details.seniority?.choice && (
                <p className="text-xs text-ink-500">Seniority detected: {c.details.seniority.choice}</p>
              )}
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <TrackerPanel job={job} onChanged={load} onError={report} />
        <SkillsPanel jobId={job.id} />
      </div>

      <AnswerAssistant job={job} aiProhibited={aiProhibited} onSaved={load} />

      <Card>
        <h2 className="mb-3 text-lg font-semibold">Job description</h2>
        <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-700 dark:text-ink-300">
          {job.description}
        </div>
      </Card>
    </div>
  );
}

function AnswerAssistant({ job, aiProhibited, onSaved }: { job: JobDetail; aiProhibited: boolean; onSaved: () => void }) {
  const [pasted, setPasted] = useState("");
  const [questions, setQuestions] = useState<FormQuestion[] | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>(job.drafts);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(aiProhibited);
  const [message, setMessage] = useState<{ tone: "error" | "warn" | "info"; text: string } | null>(null);

  if (blocked) {
    return (
      <Card className="space-y-2">
        <h2 className="text-lg font-semibold">Help me answer</h2>
        <Alert tone="warn">
          This employer asks applicants <strong>not to use AI tools</strong> for their application (in the posting or
          on the application form). Kestrel won&apos;t draft answers for this job. Please write them yourself.
        </Alert>
      </Card>
    );
  }

  const loadQuestions = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const r = await api<{ questions: FormQuestion[]; message?: string; ai_prohibited?: boolean }>(
        `/jobs/${job.id}/questions`,
      );
      if (r.ai_prohibited) {
        setBlocked(true);
        return;
      }
      setQuestions(r.questions);
      if (r.message) setMessage({ tone: "info", text: r.message });
    } catch (e) {
      setMessage({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const body = questions && questions.length ? { questions } : { pasted };
      const r = await post<{ drafts: Draft[] }>(`/jobs/${job.id}/answers`, body);
      setDrafts(r.drafts);
      onSaved();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409) setBlocked(true);
      else setMessage({ tone: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Help me answer</h2>
        <p className="text-sm text-ink-600 dark:text-ink-400">
          Suggestions use only facts from your profile. Review and edit every answer, fill any{" "}
          <code className="rounded bg-ink-100 px-1 dark:bg-ink-800">[fill in: …]</code> placeholders, then copy them
          into the employer&apos;s form yourself. Demographic questions are never drafted.
        </p>
      </div>

      {message && <Alert tone={message.tone}>{message.text}</Alert>}

      {questions && questions.length > 0 ? (
        <div className="space-y-1 text-sm">
          <p className="font-medium">{questions.length} questions from the application form:</p>
          <ul className="list-inside list-disc text-ink-600 dark:text-ink-400">
            {questions.map((q, i) => (
              <li key={i}>
                {q.label}
                {q.required && " *"}
                {q.kind === "eeo_demographic" && <span className="text-xs"> (voluntary, you answer)</span>}
                {q.kind === "file_upload" && <span className="text-xs"> (upload yourself)</span>}
                {q.kind === "attestation" && <span className="text-xs"> (read and confirm yourself)</span>}
                {q.kind === "auto_filled" && <span className="text-xs"> (filled in by the form)</span>}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <textarea
          className={inputCls}
          rows={5}
          placeholder={"Paste the form's questions, one per line. Mark required ones with *\nWhy do you want to work at this company?*\nHow many years of Python experience do you have?"}
          value={pasted}
          onChange={(e) => setPasted(e.target.value)}
        />
      )}

      <div className="flex flex-wrap gap-2">
        {job.questions_available && !questions && (
          <Button variant="secondary" onClick={loadQuestions} disabled={busy}>
            Load questions from the form
          </Button>
        )}
        <Button onClick={generate} disabled={busy || (!pasted.trim() && !(questions && questions.length))}>
          {busy ? "Working…" : "Suggest answers"}
        </Button>
      </div>

      {drafts.length > 0 && (
        <div className="space-y-3">
          {drafts.map((d) => (
            <DraftItem key={d.id} draft={d} onError={(m) => setMessage({ tone: "error", text: m })} />
          ))}
        </div>
      )}
    </Card>
  );
}

function DraftItem({ draft, onError }: { draft: Draft; onError: (message: string) => void }) {
  const [text, setText] = useState(draft.answer);
  const [saved, setSaved] = useState(true);
  const skipped = ["eeo_demographic", "file_upload", "attestation", "auto_filled"].includes(draft.kind);
  return (
    <div className="rounded-lg border border-ink-200 p-3 dark:border-ink-700">
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="text-sm font-medium">{draft.question}</p>
        {!skipped && <CopyButton text={text} />}
      </div>
      {skipped ? (
        <p className="text-sm text-ink-500">{draft.note}</p>
      ) : (
        <>
          <textarea
            className={inputCls}
            rows={Math.min(10, Math.max(2, Math.ceil(text.length / 90)))}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setSaved(false);
            }}
            onBlur={async () => {
              if (!saved) {
                try {
                  await put(`/drafts/${draft.id}`, { answer: text });
                  setSaved(true);
                } catch (e) {
                  onError(`Couldn't save your edit: ${errorMessage(e)}`);
                }
              }
            }}
          />
          {(draft.note || text.includes("[fill in")) && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
              {text.includes("[fill in") ? "Contains placeholders you must complete." : draft.note}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function TrackerPanel({
  job,
  onChanged,
  onError,
}: {
  job: JobDetail;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [notes, setNotes] = useState(job.notes);
  const [saved, setSaved] = useState(true);
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Your application</h2>
        <FeedbackButtons job={job} onChanged={onChanged} onError={onError} />
      </div>
      <StatusSelect
        value={job.status}
        onChange={async (s: JobStatus) => {
          try {
            await patch(`/jobs/${job.id}`, { status: s });
          } catch (e) {
            onError(jobErrorMessage(e));
          }
          onChanged();
        }}
      />
      {job.status_changed_at && (
        <p className="text-xs text-ink-500">Status updated {new Date(job.status_changed_at).toLocaleString()}</p>
      )}
      <textarea
        className={inputCls}
        rows={4}
        placeholder="Private notes: who referred you, interview dates, follow-ups…"
        value={notes}
        onChange={(e) => {
          setNotes(e.target.value);
          setSaved(false);
        }}
        onBlur={async () => {
          if (!saved) {
            try {
              await patch(`/jobs/${job.id}`, { notes });
              setSaved(true);
            } catch (e) {
              onError(`Couldn't save your notes: ${jobErrorMessage(e)}`);
            }
          }
        }}
      />
      <p className="text-xs text-ink-500">Stored only on this computer. Kestrel never contacts the employer.</p>
    </Card>
  );
}

function SkillsPanel({ jobId }: { jobId: number }) {
  const [data, setData] = useState<{ matched: string[]; missing: string[] | null; message: string | null } | null>(
    null,
  );
  const load = useCallback(async () => {
    try {
      setData(await api(`/jobs/${jobId}/skills`));
    } catch (e) {
      setData({ matched: [], missing: null, message: jobErrorMessage(e) });
    }
  }, [jobId]);
  useLoader(load);

  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold">Skills</h2>
      {!data ? (
        <p className="text-sm text-ink-500">Checking the posting…</p>
      ) : (
        <>
          <div>
            <h3 className="mb-1 text-sm font-medium text-ink-500">You have</h3>
            {data.matched.length ? (
              <div className="flex flex-wrap gap-1">
                {data.matched.map((s) => (
                  <span
                    key={s}
                    className="rounded-md bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                  >
                    ✓ {s}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-ink-500">None of your listed skills are named in the posting.</p>
            )}
          </div>
          <div>
            <h3 className="mb-1 text-sm font-medium text-ink-500">Asked for, not in your profile</h3>
            {data.missing === null ? (
              <p className="text-sm text-ink-500">{data.message}</p>
            ) : data.missing.length ? (
              <div className="flex flex-wrap gap-1">
                {data.missing.map((s) => (
                  <span
                    key={s}
                    className="rounded-md bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200"
                  >
                    {s}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-ink-500">Nothing obvious. You cover what the posting asks for.</p>
            )}
            <p className="mt-2 text-xs text-ink-500">
              If you do have one of these, add it to your profile so matching and answers can use it.
            </p>
          </div>
        </>
      )}
    </Card>
  );
}
