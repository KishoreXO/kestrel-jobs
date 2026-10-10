"use client";

import { useCallback, useEffect, useState } from "react";
import { useLoader } from "@/lib/hooks";
import { Alert, Button, Card, Field, ListInput, inputCls } from "@/components/ui";
import { api, errorMessage, post, put } from "@/lib/api";
import { ModelFields, TestButton } from "@/components/model-fields";

type Settings = Record<string, string | number | boolean | string[]>;
interface SettingsResponse {
  settings: Settings;
  sources: { name: string; terms_url: string; attribution: string; enabled: boolean }[];
  denylist: string[];
}
interface AuditEntry {
  id: number;
  ts: string;
  method: string;
  url: string;
  purpose: string;
  status: number | null;
  blocked_reason: string;
}

const SECRET_KEYS = ["typesafe_api_key", "llm_api_key", "classifier_llm_api_key", "usajobs_api_key", "adzuna_app_key"];

export default function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [form, setForm] = useState<Settings>({});
  const [msg, setMsg] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<SettingsResponse>("/settings");
      setData(r);
      const f = { ...r.settings };
      for (const k of SECRET_KEYS) f[k] = "";
      setForm(f);
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    }
  }, []);

  useLoader(load);

  if (!data) return msg ? <Alert tone="error">{msg.text}</Alert> : <p className="text-sm text-ink-500">Loading…</p>;

  const s = data.settings;
  const set = (k: string, v: Settings[string]) => setForm((f) => ({ ...f, [k]: v }));
  const setMany = (changes: Settings) => setForm((f) => ({ ...f, ...changes }));
  // Saves without resetting the page, so typed values stay put while testing a connection.
  const persist = async () => {
    // The reply also says which sources are active now, so that list never shows stale states.
    setData(await put<SettingsResponse>("/settings", form));
    setForm((f) => {
      const next = { ...f };
      for (const k of SECRET_KEYS) next[k] = "";
      return next;
    });
  };
  const text = (k: string, placeholder = "", type = "text") => (
    <input
      className={inputCls}
      type={type}
      value={String(form[k] ?? "")}
      placeholder={SECRET_KEYS.includes(k) && s[k] === "set" ? "•••••••• (saved, leave blank to keep)" : placeholder}
      onChange={(e) => set(k, type === "number" ? Number(e.target.value) : e.target.value)}
      autoComplete="off"
    />
  );
  const check = (k: string, label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={Boolean(form[k])} onChange={(e) => set(k, e.target.checked)} />
      {label}
    </label>
  );

  const save = async () => {
    try {
      await persist();
      setMsg({ tone: "success", text: "Settings saved." });
    } catch (e) {
      setMsg({ tone: "error", text: (e as Error).message });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Settings</h1>
          <p className="text-sm text-ink-600 dark:text-ink-400">Keys are stored only on this machine.</p>
        </div>
        <Button onClick={save}>Save settings</Button>
      </div>

      {msg && <Alert tone={msg.tone}>{msg.text}</Alert>}

      <Card className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">Identity</h2>
        <Field label="Contact email" hint="Included in the User-Agent so site operators can reach you. Strongly recommended.">
          {text("contact_email", "you@example.com")}
        </Field>
      </Card>

      <Card className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">Classifier</h2>
        <Field
          label="Backend"
          hint="Jev or another classification/decision model is recommended. General chat models also work, but are slower and less calibrated."
        >
          <select className={inputCls} value={String(form.classifier)} onChange={(e) => set("classifier", e.target.value)}>
            <option value="jev">Jev by TypeSafe (Recommended)</option>
            <option value="llm">Another model you choose (hosted API or local)</option>
          </select>
        </Field>
        {form.classifier === "jev" ? (
          <>
            <Field label="TypeSafe API key" hint={<a className="underline" href="https://console.typesafe.ai/keys" target="_blank" rel="noopener noreferrer">Get a key</a>}>
              {text("typesafe_api_key")}
            </Field>
            <Field label="Jev model">{text("jev_model", "jev-latest")}</Field>
          </>
        ) : (
          <>
            <p className="text-xs text-ink-500 sm:col-span-2">
              Jev or another classification/decision model is recommended: they&apos;re built for typed judgements and
              return calibrated probabilities. Leave these blank to reuse the answer model below, or fill only the model to use a smaller one on the same server.
            </p>
            <ModelFields prefix="classifier_" target="classifier" form={form} saved={s} set={setMany} save={persist} inherit />
          </>
        )}
        <div className="sm:col-span-2">
          <TestButton target="classifier" save={persist} />
        </div>
      </Card>

      <Card className="grid gap-4 sm:grid-cols-2">
        <h2 className="text-lg font-semibold sm:col-span-2">Model for answers &amp; resume import</h2>
        <p className="text-xs text-ink-500 sm:col-span-2">
          Any model you like, hosted or running on your own computer. Pick a provider (or a custom address), then
          click <strong>Load models</strong> to choose from the models it offers.
        </p>
        <ModelFields prefix="" target="llm" form={form} saved={s} set={setMany} save={persist} />
        <div className="sm:col-span-2">
          <TestButton target="llm" save={persist} />
        </div>
      </Card>

      <Card className="space-y-4">
        <h2 className="text-lg font-semibold">Sources</h2>
        <div className="grid gap-2 sm:grid-cols-3">
          {check("enable_greenhouse", "Greenhouse boards")}
          {check("enable_lever", "Lever boards")}
          {check("enable_ashby", "Ashby boards")}
        </div>
        <div className="grid gap-4 border-t border-ink-200 pt-4 sm:grid-cols-3 dark:border-ink-700">
          <div className="sm:col-span-3">{check("enable_usajobs", "USAJOBS (requires your own free key)")}</div>
          <Field label="USAJOBS API key">{text("usajobs_api_key")}</Field>
          <Field label="Registered email">{text("usajobs_email")}</Field>
        </div>
        <div className="grid gap-4 border-t border-ink-200 pt-4 sm:grid-cols-3 dark:border-ink-700">
          <div className="sm:col-span-3">{check("enable_adzuna", "Adzuna (requires your own free key)")}</div>
          <Field label="App ID">{text("adzuna_app_id")}</Field>
          <Field label="App key">{text("adzuna_app_key")}</Field>
          <Field label="Country code">{text("adzuna_country", "us")}</Field>
        </div>
        <ul className="space-y-1 border-t border-ink-200 pt-4 text-xs text-ink-500 dark:border-ink-700">
          {data.sources.map((src) => (
            <li key={src.name}>
              <span className="font-medium">{src.name}</span>: {src.enabled ? "active" : "inactive"} ·{" "}
              <a className="underline" href={src.terms_url} target="_blank" rel="noopener noreferrer">
                terms
              </a>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="grid gap-4 sm:grid-cols-3">
        <h2 className="text-lg font-semibold sm:col-span-3">Scanning</h2>
        <div className="pt-6">{check("auto_scan", "Scan automatically")}</div>
        <Field label="Every (hours)" hint="Minimum 6.">
          {text("scan_interval_hours", "12", "number")}
        </Field>
        <Field label="Keyword prefilter" hint="Free local filter before any model call.">
          <select className={inputCls} value={String(form.prefilter)} onChange={(e) => set("prefilter", e.target.value)}>
            <option value="loose">Loose (title matches a target title or skill)</option>
            <option value="strict">Strict (title matches a target title)</option>
            <option value="off">Off (classify everything)</option>
          </select>
        </Field>
      </Card>

      <BrowsingCard form={form} set={set} check={check} text={text} />

      <Card className="space-y-2">
        <h2 className="text-lg font-semibold">Never contacted</h2>
        <p className="text-sm text-ink-600 dark:text-ink-400">
          These sites prohibit bots in their terms, so Kestrel never sends them any request.
        </p>
        <p className="text-xs text-ink-500">{data.denylist.join(" · ")}</p>
      </Card>

      <AuditCard />

      <div className="flex justify-end">
        <Button onClick={save}>Save settings</Button>
      </div>
    </div>
  );
}

function BrowsingCard({
  form,
  set,
  check,
  text,
}: {
  form: Settings;
  set: (k: string, v: Settings[string]) => void;
  check: (k: string, label: string) => React.ReactNode;
  text: (k: string, placeholder?: string, type?: string) => React.ReactNode;
}) {
  const [status, setStatus] = useState<{ running: boolean; log: string[] } | null>(null);

  useEffect(() => {
    api<{ running: boolean; log: string[] }>("/browse/status").then(setStatus).catch(() => {});
  }, []);

  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(
      () =>
        api<{ running: boolean; log: string[] }>("/browse/status")
          .then(setStatus)
          .catch(() => {}),
      3000,
    );
    return () => clearInterval(t);
  }, [status?.running]);

  return (
    <Card className="grid gap-4 sm:grid-cols-2">
      <h2 className="text-lg font-semibold sm:col-span-2">Read-only browsing (optional)</h2>
      <Alert tone="info">
        For company careers pages without an official API. Only the sites you list are read. Sites that forbid bots are
        never visited, robots.txt is respected, pages load at most every 10 seconds, and a CAPTCHA or login wall stops it
        immediately. It never clicks, types or applies.
      </Alert>
      <div className="space-y-3">
        {check("browse_enabled", "Enable browsing")}
        <Field label="Browser backend">
          <select className={inputCls} value={String(form.browser)} onChange={(e) => set("browser", e.target.value)}>
            <option value="cua">Cua Driver (Recommended)</option>
            <option value="playwright">Playwright (headless Chromium)</option>
          </select>
        </Field>
        <Field label="Max pages per site per run">{text("browse_max_pages_per_domain", "15", "number")}</Field>
      </div>
      <Field label="Sites to read" hint="One domain or start URL per line, e.g. https://example.com/careers">
        <ListInput
          value={(form.browse_domains as string[]) || []}
          onChange={(v) => set("browse_domains", v)}
          rows={6}
        />
      </Field>
      <div className="space-y-2 sm:col-span-2">
        <Button
          variant="secondary"
          disabled={!form.browse_enabled || status?.running}
          onClick={async () => {
            try {
              await post("/browse");
              setStatus({ running: true, log: [] });
            } catch (e) {
              setStatus({ running: false, log: [errorMessage(e)] });
            }
          }}
        >
          {status?.running ? "Browsing…" : "Browse now (save settings first)"}
        </Button>
        {status && status.log.length > 0 && (
          <ul className="space-y-1 font-mono text-xs text-ink-600 dark:text-ink-400">
            {status.log.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function AuditCard() {
  const [audit, setAudit] = useState<{ entries: AuditEntry[]; total: number; blocked: number } | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (open)
      api<{ entries: AuditEntry[]; total: number; blocked: number }>("/audit")
        .then(setAudit)
        .catch(() => {});
  }, [open]);
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Audit log</h2>
          <p className="text-sm text-ink-600 dark:text-ink-400">Every outbound request, including blocked ones.</p>
        </div>
        <Button variant="secondary" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "Show"}
        </Button>
      </div>
      {open && audit && (
        <>
          <p className="text-xs text-ink-500">
            {audit.total} requests logged · {audit.blocked} blocked by policy
          </p>
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-ink-500">
                <tr>
                  <th className="py-1 pr-2">Time</th>
                  <th className="pr-2">Purpose</th>
                  <th className="pr-2">URL</th>
                  <th className="pr-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {audit.entries.map((e) => (
                  <tr key={e.id} className="border-t border-ink-100 dark:border-ink-800">
                    <td className="whitespace-nowrap py-1 pr-2">{new Date(e.ts).toLocaleString()}</td>
                    <td className="pr-2">{e.purpose}</td>
                    <td className="max-w-xs truncate pr-2 font-mono" title={e.url}>
                      {e.url}
                    </td>
                    <td className={e.blocked_reason ? "text-rose-600" : ""}>{e.blocked_reason || e.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
