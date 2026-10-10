"use client";

import { useId, useRef, useState } from "react";
import { Button, Field, inputCls } from "@/components/ui";
import { api, post } from "@/lib/api";

type Value = string | number | boolean | string[];

/** Sent instead of a key to delete the saved one (a blank key means "keep the saved key"). */
export const CLEAR_SECRET = "__clear__";

interface Provider {
  group: "On your computer" | "Hosted";
  name: string;
  url: string;
  note: string;
  model?: string;
}

// Ready-made addresses for popular providers. Every one but Anthropic accepts the common
// /v1/chat/completions format; Anthropic's own Messages API is picked automatically for its address.
export const PROVIDERS: Provider[] = [
  { group: "On your computer", name: "Ollama", url: "http://localhost:11434/v1", note: "No key needed.", model: "llama3.1:8b" },
  { group: "On your computer", name: "LM Studio", url: "http://localhost:1234/v1", note: "No key needed. Start the server in LM Studio's Developer tab." },
  { group: "On your computer", name: "llama.cpp server", url: "http://localhost:8080/v1", note: "No key needed." },
  { group: "On your computer", name: "vLLM", url: "http://localhost:8001/v1", note: "Start vLLM on port 8001 (Kestrel uses 8000)." },
  { group: "Hosted", name: "Anthropic", url: "https://api.anthropic.com/v1", note: "Needs an Anthropic API key." },
  { group: "Hosted", name: "OpenRouter (hundreds of models)", url: "https://openrouter.ai/api/v1", note: "Needs an API key." },
  { group: "Hosted", name: "Groq", url: "https://api.groq.com/openai/v1", note: "Needs an API key." },
  { group: "Hosted", name: "Mistral", url: "https://api.mistral.ai/v1", note: "Needs an API key." },
  { group: "Hosted", name: "Together AI", url: "https://api.together.xyz/v1", note: "Needs an API key." },
  { group: "Hosted", name: "DeepSeek", url: "https://api.deepseek.com/v1", note: "Needs an API key." },
  { group: "Hosted", name: "Google Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", note: "Needs an API key." },
  { group: "Hosted", name: "OpenAI", url: "https://api.openai.com/v1", note: "Needs an API key." },
];

const CUSTOM = "custom";
const clean = (url: string) => url.trim().replace(/\/+$/, "");
const host = (url: string) => {
  try {
    return new URL(clean(url)).host;
  } catch {
    return "";
  }
};

/**
 * Provider, address, model and key for one model setting.
 * `prefix` is "" for the answer model and "classifier_" for the classifier.
 */
export function ModelFields({
  prefix,
  target,
  form,
  saved,
  set,
  save,
  inherit = false,
}: {
  prefix: "" | "classifier_";
  target: "llm" | "classifier";
  form: Record<string, Value>;
  saved: Record<string, Value>;
  set: (changes: Record<string, Value>) => void;
  save: () => Promise<unknown>;
  /** Blank fields reuse the answer model's settings. */
  inherit?: boolean;
}) {
  const k = {
    url: `${prefix}llm_base_url`,
    model: `${prefix}llm_model`,
    key: `${prefix}llm_api_key`,
    format: `${prefix}llm_api_format`,
  };
  const url = String(form[k.url] ?? "");
  const key = String(form[k.key] ?? "");
  const keySaved = saved[k.key] === "set";
  const [choice, setChoice] = useState(() => PROVIDERS.find((p) => p.url === clean(url))?.url ?? CUSTOM);
  const [models, setModels] = useState<string[]>([]);
  const [modelsMsg, setModelsMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const urlRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const provider = PROVIDERS.find((p) => p.url === choice);

  // A key belongs to one provider: never send it to a different address.
  const changeUrl = (next: string, extra: Record<string, Value> = {}) => {
    const changes: Record<string, Value> = { [k.url]: next, ...extra };
    if (keySaved && !key && host(next) !== host(String(saved[k.url] ?? ""))) changes[k.key] = CLEAR_SECRET;
    if (key === CLEAR_SECRET && host(next) === host(String(saved[k.url] ?? ""))) changes[k.key] = "";
    set(changes);
    setModels([]);
    setModelsMsg("");
  };

  const pick = (value: string) => {
    setChoice(value);
    if (value === CUSTOM) {
      // Keep whatever address is there so it can be edited.
      setTimeout(() => urlRef.current?.focus(), 0);
      return;
    }
    const p = PROVIDERS.find((x) => x.url === value)!;
    changeUrl(p.url, { [k.format]: "auto", ...(p.model && !form[k.model] ? { [k.model]: p.model } : {}) });
  };

  const loadModels = async () => {
    setLoading(true);
    setModelsMsg("");
    try {
      await save();
      const r = await api<{ ok: boolean; models: string[]; message: string }>(`/settings/models?target=${target}`);
      setModels(r.models);
      setModelsMsg(
        r.ok ? (r.models.length ? `${r.models.length} models found. Pick one in the Model box.` : "The server lists no models.") : r.message,
      );
    } catch (e) {
      setModelsMsg((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Field label="Provider" hint={provider?.note}>
        <select className={inputCls} value={choice} onChange={(e) => pick(e.target.value)}>
          <option value={CUSTOM}>{inherit ? "Same as answer model, or a custom address" : "Custom address"}</option>
          {(["On your computer", "Hosted"] as const).map((g) => (
            <optgroup key={g} label={g}>
              {PROVIDERS.filter((p) => p.group === g).map((p) => (
                <option key={p.url} value={p.url}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </Field>
      <Field
        label="Base URL"
        hint={
          choice === CUSTOM
            ? "Any server that accepts /v1/chat/completions requests. Include the /v1, e.g. http://localhost:1234/v1"
            : undefined
        }
      >
        <input
          ref={urlRef}
          className={inputCls}
          value={url}
          placeholder={inherit ? "same as answer model" : "http://localhost:11434/v1"}
          onChange={(e) => {
            const next = e.target.value;
            setChoice(PROVIDERS.find((p) => p.url === clean(next))?.url ?? CUSTOM);
            changeUrl(next);
          }}
          autoComplete="off"
          spellCheck={false}
        />
      </Field>
      <Field label="Model" hint={modelsMsg || undefined}>
        <div className="flex gap-2">
          <input
            className={inputCls}
            list={listId}
            value={String(form[k.model] ?? "")}
            placeholder={inherit ? "same as answer model" : "e.g. llama3.1:8b"}
            onChange={(e) => set({ [k.model]: e.target.value })}
            autoComplete="off"
            spellCheck={false}
          />
          <datalist id={listId}>
            {models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <Button variant="secondary" className="shrink-0" onClick={loadModels} disabled={loading} type="button">
            {loading ? "Loading…" : "Load models"}
          </Button>
        </div>
      </Field>
      <Field
        label="API key"
        hint={
          key === CLEAR_SECRET ? (
            <span className="text-amber-700 dark:text-amber-300">
              The saved key will be removed when you save, so it is never sent to a different provider.
            </span>
          ) : keySaved && !key ? (
            <button type="button" className="underline" onClick={() => set({ [k.key]: CLEAR_SECRET })}>
              Remove saved key
            </button>
          ) : (
            "Leave blank for local servers."
          )
        }
      >
        <input
          className={inputCls}
          type="password"
          value={key === CLEAR_SECRET ? "" : key}
          placeholder={keySaved && key !== CLEAR_SECRET ? "•••••••• (saved, leave blank to keep)" : ""}
          onChange={(e) => set({ [k.key]: e.target.value })}
          autoComplete="off"
        />
      </Field>
      {choice === CUSTOM && (
        <Field label="Request format" hint="Leave on Automatic unless your server is a proxy for the Anthropic API.">
          <select className={inputCls} value={String(form[k.format] ?? "auto")} onChange={(e) => set({ [k.format]: e.target.value })}>
            <option value="auto">Automatic</option>
            <option value="chat_completions">/v1/chat/completions (most servers)</option>
            <option value="anthropic">Anthropic Messages API</option>
          </select>
        </Field>
      )}
    </>
  );
}

export function TestButton({ target, save }: { target: "llm" | "classifier"; save: () => Promise<unknown> }) {
  const [state, setState] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="secondary"
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setState(null);
          try {
            await save();
            setState(await post<{ ok: boolean; message: string }>("/settings/test", { target }));
          } catch (e) {
            setState({ ok: false, message: (e as Error).message });
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Testing…" : "Save & test connection"}
      </Button>
      {state && (
        <span
          role="status"
          className={`text-sm ${state.ok ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300"}`}
        >
          {state.ok ? "✓ " : "✗ "}
          {state.message}
        </span>
      )}
    </div>
  );
}
