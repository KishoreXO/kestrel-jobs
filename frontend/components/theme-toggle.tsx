"use client";

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "kestrel-theme";
const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };

function readTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/** Runs before paint (see layout.tsx) so the page never flashes the wrong theme. */
export const themeScript = `(function(){try{var t=localStorage.getItem("${KEY}");var d=t==="dark"||(t!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d)}catch(e){}})()`;

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const initial = readTheme();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync with the stored preference after hydration
    setTheme(initial);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply(readTheme());
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const next = () => {
    const t = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
    setTheme(t);
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* storage unavailable: still apply for this visit */
    }
    apply(t);
  };

  return (
    <button
      onClick={next}
      title="Switch theme"
      className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-ink-600 hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
    >
      <svg viewBox="0 0 20 20" className="h-4 w-4" fill="currentColor" aria-hidden="true">
        {theme === "dark" ? (
          <path d="M17.3 13.4A7.5 7.5 0 0 1 6.6 2.7a7.5 7.5 0 1 0 10.7 10.7z" />
        ) : theme === "light" ? (
          <path d="M10 14a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm0-14a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V1a1 1 0 0 1 1-1zm0 17a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1zM0 10a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2H1a1 1 0 0 1-1-1zm17 0a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2h-1a1 1 0 0 1-1-1z" />
        ) : (
          <path d="M10 2a8 8 0 1 0 0 16V2z" />
        )}
      </svg>
      {LABEL[theme]}
    </button>
  );
}
