/** Kestrel mark: a hovering kestrel (swept wings, stooping body) above the one target it picked out. */
export function LogoMark({ className = "h-7 w-7" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" className="fill-accent-600 dark:fill-accent-500" />
      <path d="M3.5 10.5 16 16.2l12.5-5.7-5.6-.4L16 13.3 9.1 10.1z" className="fill-white dark:fill-ink-950" />
      <path d="M13.6 15.2h4.8L16 21.6z" className="fill-white dark:fill-ink-950" />
      <circle cx="16" cy="26" r="2.1" className="fill-accent-100 dark:fill-ink-900" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2">
      <LogoMark />
      <span className="text-lg font-semibold tracking-tight text-ink-900 dark:text-white">Kestrel</span>
    </span>
  );
}
