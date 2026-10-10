// Thin client for the local Kestrel backend (proxied by the dashboard server at /api).

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: isForm ? init?.headers : { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      message = detailText((await res.json()).detail) || message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

/** The backend's error detail as one readable line (validation errors arrive as a list of fields). */
function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = (d.loc ?? []).filter((p) => p !== "body").join(".");
        return field ? `${field}: ${d.msg ?? "invalid"}` : (d.msg ?? "invalid");
      })
      .join("; ");
  }
  return detail === undefined ? "" : JSON.stringify(detail);
}

/** A readable message for any error thrown by the helpers above (or by fetch itself). */
export const errorMessage = (e: unknown): string =>
  e instanceof ApiError && e.status === 404 ? "Not found." : e instanceof Error ? e.message : String(e);

/** Same, for actions on a job: a 404 there means the job was removed since the page was loaded. */
export const jobErrorMessage = (e: unknown): string =>
  e instanceof ApiError && e.status === 404
    ? "That job no longer exists (it may have been removed)."
    : errorMessage(e);

/** Links in postings come from third parties: only http(s) links are ever rendered as clickable. */
export const safeUrl = (url: string | null | undefined): string | undefined => {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
};

export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export type Tier = "strong" | "good" | "maybe" | "rejected" | "pending";

export interface Classification {
  tier: Tier;
  match_score: number;
  confidence: number;
  reject_reason: string;
  ai_prohibited: number;
  backend: string;
  model: string;
  details: Record<string, { type: string; value?: number; choice?: string; confidence?: number }>;
}

/** Skills from your profile found in the posting (stored next to the typed answers). */
export const matchedSkills = (c: Classification | null): string[] =>
  ((c?.details as unknown as { matched_skills?: string[] }) ?? {}).matched_skills ?? [];

export type JobStatus = "new" | "saved" | "applied" | "interviewing" | "offer" | "rejected" | "archived";

export const STATUS_LABELS: Record<JobStatus, string> = {
  new: "New",
  saved: "Saved",
  applied: "Applied",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Not selected",
  archived: "Archived",
};

export interface Job {
  id: number;
  source: string;
  company: string;
  title: string;
  location: string;
  remote: boolean | null;
  url: string;
  apply_url: string;
  salary: string;
  department: string;
  posted_at: string | null;
  attribution_text: string;
  attribution_url: string;
  snippet?: string;
  description?: string;
  hidden: boolean;
  status: JobStatus;
  notes: string;
  feedback: number;
  first_seen: string | null;
  fetched_at: string;
  status_changed_at: string | null;
  classification: Classification | null;
}

export interface Draft {
  id: number;
  question: string;
  kind: string;
  answer: string;
  note: string;
}

export interface FormQuestion {
  label: string;
  required: boolean;
  kind: string;
  options: string[];
  description: string;
}

export const pct = (n: number | undefined | null) => `${Math.round((n ?? 0) * 100)}%`;
