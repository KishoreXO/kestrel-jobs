"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Logo } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";

const LINKS = [
  { href: "/jobs", label: "Jobs" },
  { href: "/profile", label: "Profile" },
  { href: "/companies", label: "Companies" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-10 border-b border-ink-200 bg-white/85 backdrop-blur dark:border-ink-800 dark:bg-ink-950/85">
      <nav className="mx-auto flex max-w-5xl flex-wrap items-center gap-1 px-4 py-3">
        <Link href="/jobs" className="mr-4">
          <Logo />
        </Link>
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
              path.startsWith(l.href)
                ? "bg-accent-50 text-accent-800 dark:bg-accent-950 dark:text-accent-200"
                : "text-ink-600 hover:text-ink-900 dark:text-ink-400 dark:hover:text-white"
            }`}
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </nav>
    </header>
  );
}
