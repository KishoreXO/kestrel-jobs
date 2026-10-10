"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Card, inputCls } from "@/components/ui";
import { api, errorMessage, put } from "@/lib/api";

interface Company {
  name: string;
  ats: "greenhouse" | "lever" | "ashby";
  slug: string;
  region: string;
}

const PATTERNS: [RegExp, Company["ats"]][] = [
  [/greenhouse\.io\/(?:embed\/job_board\?for=)?([\w-]+)/, "greenhouse"],
  [/jobs\.(?:eu\.)?lever\.co\/([\w.-]+)/, "lever"],
  [/jobs\.ashbyhq\.com\/([\w.%-]+)/, "ashby"],
];

/** "acme-labs" → "Acme Labs" (editable afterwards). */
function displayName(slug: string): string {
  let text = slug;
  try {
    text = decodeURIComponent(slug);
  } catch {
    /* keep the raw slug */
  }
  return text.replace(/[-_.]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).trim();
}

function fromUrl(url: string): Company | null {
  for (const [re, ats] of PATTERNS) {
    const m = url.match(re);
    if (m) return { name: displayName(m[1]), ats, slug: m[1], region: url.includes("eu.lever.co") ? "eu" : "" };
  }
  return null;
}

export default function CompaniesPage() {
  const [rows, setRows] = useState<Company[]>([]);
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    api<Company[]>("/companies")
      .then(setRows)
      .catch((e) => setMsg({ tone: "error", text: e.message }));
  }, []);

  const update = (i: number, patch: Partial<Company>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = async () => {
    try {
      setRows(await put<Company[]>("/companies", rows.filter((r) => r.slug)));
      setMsg({ tone: "success", text: "Saved. These boards are read on the next scan (each at most every 6 hours)." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Companies</h1>
        <p className="text-sm text-ink-600 dark:text-ink-400">
          Companies whose public Greenhouse, Lever or Ashby job boards you want to watch. These are official APIs that
          the job-board vendors publish for exactly this purpose. Other sites&apos; job feeds can be added at the bottom.
        </p>
      </div>

      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      <StarterPack
        rows={rows}
        onAdd={async (picked) => {
          try {
            setRows(await put<Company[]>("/companies", [...rows.filter((r) => r.slug), ...picked]));
            setMsg({ tone: "success", text: `Added ${picked.length} ${picked.length === 1 ? "company" : "companies"}.` });
          } catch (e) {
            setMsg({ tone: "error", text: (e as Error).message });
          }
        }}
      />

      <Card className="space-y-3">
        <p className="text-sm font-medium">Add from a careers page URL</p>
        <div className="flex gap-2">
          <input
            className={inputCls}
            placeholder="https://jobs.lever.co/acme  ·  https://boards.greenhouse.io/acme  ·  https://jobs.ashbyhq.com/acme"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <Button
            variant="secondary"
            onClick={() => {
              const c = fromUrl(url);
              if (!c) {
                setMsg({ tone: "error", text: "Not a Greenhouse, Lever or Ashby job board URL." });
                return;
              }
              setRows([...rows, c]);
              setUrl("");
              setMsg(null);
            }}
          >
            Add
          </Button>
        </div>
      </Card>

      <Card className="space-y-3">
        <div className="hidden grid-cols-[1fr_9rem_1fr_5rem_4rem] gap-2 text-xs font-medium uppercase text-ink-500 sm:grid">
          <span>Name</span>
          <span>ATS</span>
          <span>Board slug</span>
          <span>Region</span>
          <span />
        </div>
        {rows.length === 0 && <p className="text-sm text-ink-500">No companies yet.</p>}
        {rows.map((r, i) => (
          <div
            key={i}
            className="grid grid-cols-2 gap-2 border-b border-ink-100 pb-3 last:border-0 sm:grid-cols-[1fr_9rem_1fr_5rem_4rem] sm:border-0 sm:pb-0 dark:border-ink-800"
          >
            <input aria-label="Company name" placeholder="Name" className={inputCls} value={r.name} onChange={(e) => update(i, { name: e.target.value })} />
            <select aria-label="Job board" className={inputCls} value={r.ats} onChange={(e) => update(i, { ats: e.target.value as Company["ats"] })}>
              <option value="greenhouse">Greenhouse</option>
              <option value="lever">Lever</option>
              <option value="ashby">Ashby</option>
            </select>
            <input aria-label="Board slug" placeholder="Board slug" className={inputCls} value={r.slug} onChange={(e) => update(i, { slug: e.target.value.trim() })} />
            <input
              aria-label="Region"
              className={inputCls}
              value={r.region}
              placeholder={r.ats === "lever" ? "eu?" : ""}
              disabled={r.ats !== "lever"}
              onChange={(e) => update(i, { region: e.target.value })}
            />
            <Button variant="ghost" aria-label={`Remove ${r.name || "company"}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}>
              ✕
            </Button>
          </div>
        ))}
        <div className="flex justify-between pt-2">
          <Button variant="secondary" onClick={() => setRows([...rows, { name: "", ats: "greenhouse", slug: "", region: "" }])}>
            Add row
          </Button>
          <Button onClick={save}>Save companies</Button>
        </div>
      </Card>

      <Feeds />
    </div>
  );
}

interface Feed {
  name: string;
  url: string;
  allowed: boolean;
}

/** Job feeds (RSS, Atom or JSON Feed) that a site publishes for others to read. */
function Feeds() {
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    api<Feed[]>("/feeds")
      .then(setFeeds)
      .catch((e) => setMsg({ tone: "error", text: errorMessage(e) }));
  }, []);

  const update = (i: number, change: Partial<Feed>) => setFeeds(feeds.map((f, j) => (j === i ? { ...f, ...change } : f)));

  const save = async () => {
    try {
      setFeeds(await put<Feed[]>("/feeds", feeds.filter((f) => f.url.trim())));
      setMsg({ tone: "success", text: "Saved. Each feed is read on the next scan (at most every 6 hours)." });
    } catch (e) {
      setMsg({ tone: "error", text: errorMessage(e) });
    }
  };

  return (
    <Card className="space-y-3">
      <div>
        <p className="text-sm font-medium">Other job feeds</p>
        <p className="text-xs text-ink-500">
          Paste an RSS, Atom or JSON job feed that a site publishes. Kestrel only reads it, follows the site&apos;s
          robots.txt and never reads sites whose terms forbid it. Check the site&apos;s terms before adding a feed.
        </p>
      </div>
      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}
      {feeds.length === 0 && <p className="text-sm text-ink-500">No feeds yet.</p>}
      {feeds.map((f, i) => (
        <div key={i} className="space-y-2 border-b border-ink-100 pb-3 last:border-0 dark:border-ink-800">
          <div className="grid gap-2 sm:grid-cols-[14rem_1fr_4rem]">
            <input aria-label="Feed name" placeholder="Name, e.g. Example Jobs" className={inputCls} value={f.name} onChange={(e) => update(i, { name: e.target.value })} />
            <input
              aria-label="Feed address"
              placeholder="https://jobs.example.org/feed.xml"
              className={inputCls}
              value={f.url}
              onChange={(e) => update(i, { url: e.target.value.trim() })}
              spellCheck={false}
            />
            <Button variant="ghost" aria-label={`Remove ${f.name || "feed"}`} onClick={() => setFeeds(feeds.filter((_, j) => j !== i))}>
              ✕
            </Button>
          </div>
          <label className="flex items-start gap-2 text-xs text-ink-600 dark:text-ink-400">
            <input type="checkbox" className="mt-0.5" checked={f.allowed} onChange={(e) => update(i, { allowed: e.target.checked })} />
            This site allows automated reading of this feed (I checked its terms).
          </label>
        </div>
      ))}
      <div className="flex justify-between pt-2">
        <Button variant="secondary" onClick={() => setFeeds([...feeds, { name: "", url: "", allowed: false }])}>
          Add feed
        </Button>
        <Button onClick={save}>Save feeds</Button>
      </div>
    </Card>
  );
}

const GROUP_LABELS: Record<string, string> = {
  software: "Software",
  data_and_ai: "Data & AI",
  finance: "Finance",
  healthcare: "Healthcare",
  consumer_and_marketplaces: "Consumer",
  education_and_nonprofit: "Education & nonprofit",
  hardware_and_mobility: "Hardware & mobility",
};

function StarterPack({ rows, onAdd }: { rows: Company[]; onAdd: (c: Company[]) => void }) {
  const [groups, setGroups] = useState<Record<string, Company[]>>({});
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api<Record<string, Company[]>>("/companies/starter")
      .then(setGroups)
      .catch(() => setGroups({}));
  }, []);
  const has = (c: Company) => rows.some((r) => r.ats === c.ats && r.slug.toLowerCase() === c.slug.toLowerCase());
  const norm = (c: Company): Company => ({ ...c, region: c.region ?? "" });
  if (!Object.keys(groups).length) return null;

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">Starter pack</p>
          <p className="text-xs text-ink-500">Well-known employers with public job boards. Click one to watch it.</p>
        </div>
        <Button variant="ghost" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide" : "Browse"}
        </Button>
      </div>
      {open &&
        Object.entries(groups).map(([group, items]) => {
          const missing = items.filter((c) => !has(c));
          return (
            <div key={group} className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">
                  {GROUP_LABELS[group] ?? group}
                </span>
                {missing.length > 0 && (
                  <button className="text-xs text-accent-700 hover:underline dark:text-accent-300" onClick={() => onAdd(missing.map(norm))}>
                    Add all {missing.length}
                  </button>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {items.map((c) => (
                  <button
                    key={`${c.ats}:${c.slug}`}
                    disabled={has(c)}
                    onClick={() => onAdd([norm(c)])}
                    className="rounded-full border border-ink-200 px-2.5 py-1 text-xs hover:border-accent-500 hover:text-accent-700 disabled:cursor-default disabled:border-accent-200 disabled:bg-accent-50 disabled:text-accent-800 dark:border-ink-700 dark:hover:text-accent-300 dark:disabled:border-accent-900 dark:disabled:bg-accent-950 dark:disabled:text-accent-200"
                  >
                    {has(c) ? "✓ " : "+ "}
                    {c.name}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
    </Card>
  );
}
