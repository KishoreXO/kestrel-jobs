import { safeUrl, type Job } from "@/lib/api";

export function Attribution({ job }: { job: Job }) {
  if (!job.attribution_text) {
    return <p className="text-xs text-ink-400">via {job.source}</p>;
  }
  const href = safeUrl(job.attribution_url);
  return (
    <p className="text-xs text-ink-500">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="font-medium underline">
          {job.attribution_text}
        </a>
      ) : (
        <span className="font-medium">{job.attribution_text}</span>
      )}
    </p>
  );
}
