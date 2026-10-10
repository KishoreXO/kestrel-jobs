"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Card, Field, ListInput, inputCls } from "@/components/ui";
import { api, put } from "@/lib/api";

interface Education {
  degree: string;
  field: string;
  institution: string;
  year: string;
}
interface Experience {
  title: string;
  company: string;
  start: string;
  end: string;
  highlights: string[];
}
interface Profile {
  name: string;
  email: string;
  phone: string;
  location: string;
  headline: string;
  summary: string;
  years_experience: number | null;
  target_titles: string[];
  target_seniority: string[];
  skills: string[];
  industries: string[];
  education: Education[];
  experience: Experience[];
  certifications: string[];
  languages: string[];
  links: Record<string, string>;
  preferred_locations: string[];
  remote_preference: "remote" | "hybrid" | "onsite" | "any";
  willing_to_relocate: boolean;
  work_authorization: string[];
  needs_sponsorship: boolean;
  salary_min: number | null;
  salary_currency: string;
  notice_period: string;
  excluded_companies: string[];
  excluded_keywords: string[];
  extra_facts: string[];
}

export default function ProfilePage() {
  const [p, setP] = useState<Profile | null>(null);
  const [version, setVersion] = useState(0); // remounts list inputs after an import
  const [msg, setMsg] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Profile>("/profile")
      .then(setP)
      .catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);

  if (!p) return msg ? <Alert tone="error">{msg.text}</Alert> : <p className="text-sm text-ink-500">Loading…</p>;

  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setP({ ...p, [k]: v });

  const upload = async (file: File) => {
    setBusy(true);
    setMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await api<{ profile: Profile; structured: boolean; message: string }>("/profile/import", {
        method: "POST",
        body: fd,
      });
      if (r.structured) {
        setP(r.profile);
        setVersion((v) => v + 1);
      }
      setMsg({ tone: r.structured ? "info" : "error", text: r.message });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    try {
      setP(await put<Profile>("/profile", p));
      setMsg({ tone: "success", text: "Profile saved. Jobs will be re-checked against it on the next scan." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    }
  };

  return (
    <div className="space-y-6" key={version}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Your profile</h1>
          <p className="text-sm text-ink-600 dark:text-ink-400">
            Matching and answer suggestions use only what&apos;s here. Stored locally on this machine.
          </p>
        </div>
        <div className="flex gap-2">
          <label className="inline-flex cursor-pointer items-center rounded-lg border border-ink-300 bg-white px-3.5 py-2 text-sm font-medium hover:bg-ink-50 dark:border-ink-600 dark:bg-ink-800">
            {busy ? "Importing…" : "Import resume (PDF/DOCX)"}
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = ""; // so picking the same file again imports it again
                if (file) void upload(file);
              }}
            />
          </label>
          <Button onClick={save}>Save profile</Button>
        </div>
      </div>

      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      <Card className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">What you&apos;re looking for</h2>
        <Field label="Target job titles" hint="One per line. These drive both search and matching.">
          <ListInput value={p.target_titles} onChange={(v) => set("target_titles", v)} placeholder="Backend Engineer" />
        </Field>
        <Field label="Skills" hint="One per line or comma-separated.">
          <ListInput value={p.skills} onChange={(v) => set("skills", v)} placeholder="Python, PostgreSQL" rows={4} />
        </Field>
        <Field label="Target seniority" hint="e.g. mid, senior">
          <ListInput value={p.target_seniority} onChange={(v) => set("target_seniority", v)} rows={2} />
        </Field>
        <Field label="Years of experience">
          <input
            className={inputCls}
            type="number"
            min={0}
            step={0.5}
            value={p.years_experience ?? ""}
            onChange={(e) => set("years_experience", e.target.value === "" ? null : Number(e.target.value))}
          />
        </Field>
        <Field label="Industries / domains">
          <ListInput value={p.industries} onChange={(v) => set("industries", v)} rows={2} />
        </Field>
        <Field label="Preferred locations">
          <ListInput value={p.preferred_locations} onChange={(v) => set("preferred_locations", v)} rows={2} />
        </Field>
        <Field label="Remote preference">
          <select
            className={inputCls}
            value={p.remote_preference}
            onChange={(e) => set("remote_preference", e.target.value as Profile["remote_preference"])}
          >
            <option value="any">Any</option>
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
            <option value="onsite">On-site</option>
          </select>
        </Field>
        <Field label="Minimum salary">
          <div className="flex gap-2">
            <input
              className={inputCls}
              type="number"
              value={p.salary_min ?? ""}
              onChange={(e) => set("salary_min", e.target.value === "" ? null : Number(e.target.value))}
            />
            <input
              className={`${inputCls} w-24`}
              value={p.salary_currency}
              onChange={(e) => set("salary_currency", e.target.value)}
            />
          </div>
        </Field>
        <Field label="Work authorization" hint="Countries/regions you can work in without sponsorship.">
          <ListInput value={p.work_authorization} onChange={(v) => set("work_authorization", v)} rows={2} />
        </Field>
        <div className="space-y-2 pt-6">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={p.needs_sponsorship} onChange={(e) => set("needs_sponsorship", e.target.checked)} />
            I need visa sponsorship
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={p.willing_to_relocate}
              onChange={(e) => set("willing_to_relocate", e.target.checked)}
            />
            Willing to relocate
          </label>
        </div>
        <Field label="Exclude companies">
          <ListInput value={p.excluded_companies} onChange={(v) => set("excluded_companies", v)} rows={2} />
        </Field>
        <Field label="Exclude keywords" hint="Titles containing these are skipped.">
          <ListInput value={p.excluded_keywords} onChange={(v) => set("excluded_keywords", v)} rows={2} />
        </Field>
      </Card>

      <Card className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">About you (used for answer suggestions)</h2>
        <Field label="Name">
          <input className={inputCls} value={p.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Location">
          <input className={inputCls} value={p.location} onChange={(e) => set("location", e.target.value)} />
        </Field>
        <Field label="Email">
          <input className={inputCls} value={p.email} onChange={(e) => set("email", e.target.value)} />
        </Field>
        <Field label="Phone">
          <input className={inputCls} value={p.phone} onChange={(e) => set("phone", e.target.value)} />
        </Field>
        <Field label="Headline">
          <input className={inputCls} value={p.headline} onChange={(e) => set("headline", e.target.value)} />
        </Field>
        <Field label="Notice period / availability">
          <input className={inputCls} value={p.notice_period} onChange={(e) => set("notice_period", e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Summary">
            <textarea className={inputCls} rows={4} value={p.summary} onChange={(e) => set("summary", e.target.value)} />
          </Field>
        </div>
        <Field label="Certifications">
          <ListInput value={p.certifications} onChange={(v) => set("certifications", v)} rows={2} />
        </Field>
        <Field label="Languages">
          <ListInput value={p.languages} onChange={(v) => set("languages", v)} rows={2} />
        </Field>
        <div className="sm:col-span-2">
          <Field
            label="Extra facts you're happy to share"
            hint="One per line, e.g. 'Available from March 2027' or 'Led a team of 4'. Answers never go beyond your profile."
          >
            <ListInput value={p.extra_facts} onChange={(v) => set("extra_facts", v)} rows={4} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Links" hint="One per line as name: url (e.g. github: https://github.com/you)">
            <ListInput
              value={Object.entries(p.links).map(([k, v]) => `${k}: ${v}`)}
              onChange={(v) =>
                set(
                  "links",
                  Object.fromEntries(
                    v
                      .map((line) => {
                        const i = line.indexOf(":");
                        return i > 0 ? [line.slice(0, i).trim(), line.slice(i + 1).trim()] : null;
                      })
                      .filter((x): x is [string, string] => !!x && !!x[1]),
                  ),
                )
              }
              rows={3}
            />
          </Field>
        </div>
      </Card>

      <Card className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Experience</h2>
          <Button
            variant="secondary"
            onClick={() =>
              set("experience", [...p.experience, { title: "", company: "", start: "", end: "", highlights: [] }])
            }
          >
            Add role
          </Button>
        </div>
        {p.experience.map((x, i) => {
          const update = (patch: Partial<Experience>) =>
            set(
              "experience",
              p.experience.map((e, j) => (j === i ? { ...e, ...patch } : e)),
            );
          return (
            <div key={i} className="grid gap-3 rounded-lg border border-ink-200 p-3 sm:grid-cols-4 dark:border-ink-700">
              <input className={inputCls} placeholder="Title" value={x.title} onChange={(e) => update({ title: e.target.value })} />
              <input className={inputCls} placeholder="Company" value={x.company} onChange={(e) => update({ company: e.target.value })} />
              <input className={inputCls} placeholder="Start" value={x.start} onChange={(e) => update({ start: e.target.value })} />
              <input className={inputCls} placeholder="End" value={x.end} onChange={(e) => update({ end: e.target.value })} />
              <div className="sm:col-span-4">
                <ListInput value={x.highlights} onChange={(v) => update({ highlights: v })} placeholder="Highlights, one per line" />
              </div>
              <Button variant="ghost" className="sm:col-span-4 justify-self-start" onClick={() => set("experience", p.experience.filter((_, j) => j !== i))}>
                Remove
              </Button>
            </div>
          );
        })}
      </Card>

      <Card className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Education</h2>
          <Button
            variant="secondary"
            onClick={() => set("education", [...p.education, { degree: "", field: "", institution: "", year: "" }])}
          >
            Add education
          </Button>
        </div>
        {p.education.map((x, i) => {
          const update = (patch: Partial<Education>) =>
            set(
              "education",
              p.education.map((e, j) => (j === i ? { ...e, ...patch } : e)),
            );
          return (
            <div key={i} className="grid gap-3 sm:grid-cols-5">
              <input className={inputCls} placeholder="Degree" value={x.degree} onChange={(e) => update({ degree: e.target.value })} />
              <input className={inputCls} placeholder="Field" value={x.field} onChange={(e) => update({ field: e.target.value })} />
              <input className={inputCls} placeholder="Institution" value={x.institution} onChange={(e) => update({ institution: e.target.value })} />
              <input className={inputCls} placeholder="Year" value={x.year} onChange={(e) => update({ year: e.target.value })} />
              <Button variant="ghost" onClick={() => set("education", p.education.filter((_, j) => j !== i))}>
                Remove
              </Button>
            </div>
          );
        })}
      </Card>

      <div className="flex justify-end">
        <Button onClick={save}>Save profile</Button>
      </div>
    </div>
  );
}
