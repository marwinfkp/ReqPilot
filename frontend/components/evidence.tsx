"use client";

// Knowledge-base evidence shown next to agent outputs (findings, risks, SDLC, workflow,
// traceability, requirements, questions). Every citation was checked by the backend
// against the passage the agent actually retrieved (backend/app/services/rag.py).
// "Verified" means the QUOTATION was found in that passage - not that the passage proves
// the finding; relevance is a model judgement or a retrieval score and needs human review.
import { BookCheck, BookOpen, BookX, CircleSlash, ServerOff } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { AuthenticityBadge } from "@/components/common";
import { label } from "@/lib/format";
import type { Citation, EvidenceStatus, RetrievalLog } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS: Record<Exclude<EvidenceStatus, "">, { text: string; title: string; cls: string; icon: LucideIcon }> = {
  verified: {
    text: "KB quote verified", icon: BookCheck, cls: "border-emerald-200 bg-emerald-50 text-emerald-800",
    title: "The cited excerpt was found word-for-word in a knowledge-base passage retrieved for this agent. This checks "
      + "the quotation only: whether the passage really supports the finding is a judgement (by the model, or by retrieval "
      + "relevance) - review the source before relying on it.",
  },
  unverified: {
    text: "KB quote not verified", icon: BookX, cls: "border-amber-200 bg-amber-50 text-amber-900",
    title: "A retrieved passage was cited, but the quoted text was not found in it. It is not treated as evidence - check the source.",
  },
  none: {
    text: "No KB evidence", icon: CircleSlash, cls: "border-slate-200 bg-slate-50 text-slate-600",
    title: "The knowledge base was searched but no passage supports this item: it is derived from the requirements and the analysis.",
  },
  unavailable: {
    text: "KB unavailable", icon: ServerOff, cls: "border-dashed border-slate-300 bg-background text-slate-500",
    title: "The knowledge base could not be searched for this agent (no allowlisted documents for the jurisdiction, or retrieval failed).",
  },
};

const VERIFICATION: Record<Citation["verification"], { text: string; cls: string }> = {
  quote_verified: { text: "✓ Quote found word-for-word in the source passage · relevance judged by the model", cls: "text-emerald-700" },
  retrieval_link: { text: "✓ Excerpt copied from the retrieved passage · linked by retrieval relevance, not judged by a model", cls: "text-emerald-700" },
  quote_not_found: { text: "✗ Quote not found in the source - closest passage shown; requires verification", cls: "text-red-700" },
};

export function EvidenceStatusBadge({ status }: { status?: EvidenceStatus }) {
  if (!status) return null;
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <span title={s.title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", s.cls)}>
      <Icon className="size-3" /> {s.text}
    </span>
  );
}

export function CitationItem({ c }: { c: Citation }) {
  const check = VERIFICATION[c.verification] ?? VERIFICATION.retrieval_link;
  return (
    <li className="rounded-md border bg-muted/30 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">{c.document_name}</span>
        <span className="font-mono text-[11px]">{c.section_ref}</span>
        {c.title && c.title !== c.section_ref && <span className="text-muted-foreground">{c.title}</span>}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <AuthenticityBadge value={c.authenticity} />
        <span className="rounded border px-1.5 py-0.5">{c.authority || label(c.doc_type)}</span>
        {c.version && <span>Version {c.version}</span>}
        {c.page ? <span>· Page {c.page}</span> : null}
        {c.jurisdiction && <span>· {c.jurisdiction}</span>}
        {c.score != null && (
          <span title="Similarity between the agent's query and this passage (retrieval score) - not a measure of factual support">
            · relevance {c.score.toFixed(2)}
          </span>
        )}
      </div>
      <p className="mt-1.5 italic">“{c.excerpt}”</p>
      <p className={cn("mt-1", check.cls)}>{check.text}</p>
      {c.model_quote && <p className="mt-0.5 text-[11px] text-red-700">Quoted by the model (not found in the source): “{c.model_quote}”</p>}
      <p className="mt-0.5 text-[11px] text-muted-foreground">
        Used by {c.agent}{c.retrieval ? ` · ${c.retrieval} retrieval` : ""}{c.claim ? ` · ${c.claim}` : ""}
      </p>
    </li>
  );
}

/** Evidence status plus the citations, collapsed by default so result lists stay compact. */
export function Evidence({ citations, status, className }: { citations?: Citation[]; status?: EvidenceStatus; className?: string }) {
  const items = citations ?? [];
  if (!items.length && !status) return null;
  if (!items.length) return <div className={cn("mt-2", className)}><EvidenceStatusBadge status={status} /></div>;
  return (
    <details className={cn("group mt-2", className)}>
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-xs">
        <EvidenceStatusBadge status={status || (items.some((c) => c.verified) ? "verified" : "unverified")} />
        <span className="inline-flex items-center gap-1 text-muted-foreground underline-offset-2 group-open:underline">
          <BookOpen className="size-3" /> {items.length} source{items.length === 1 ? "" : "s"}
        </span>
      </summary>
      <ul className="mt-2 space-y-2">{items.map((c, i) => <CitationItem key={`${c.chunk_id}-${i}`} c={c} />)}</ul>
    </details>
  );
}

/** One line per agent run: what the agent retrieved and how its citations were checked. */
export function RetrievalSummary({ logs }: { logs?: RetrievalLog[] }) {
  if (!logs?.length) return <span className="text-xs text-muted-foreground">–</span>;
  return (
    <div className="space-y-1">
      {logs.map((log, i) => (
        <details key={i} className="text-xs">
          <summary className="cursor-pointer list-none">
            {log.method === "failed" || log.method === "none"
              ? <span className="text-muted-foreground">{log.method === "failed" ? "Retrieval failed" : "No documents"}</span>
              : <span>{log.retrieved.length} passage{log.retrieved.length === 1 ? "" : "s"} · {log.method}</span>}
            {log.prompted.length > 0 && <span className="text-muted-foreground"> · {log.prompted.length} in prompt</span>}
            {(log.verified + log.linked) > 0 && <span className="text-emerald-700"> · {log.verified + log.linked} cited</span>}
            {log.unverified > 0 && <span className="text-amber-700"> · {log.unverified} unverified</span>}
            {log.rejected > 0 && <span className="text-red-700"> · {log.rejected} rejected</span>}
          </summary>
          <div className="mt-1 space-y-1 rounded-md border bg-muted/30 p-2">
            <p className="text-muted-foreground">Document types: {log.doc_types.map(label).join(", ")} · {log.queries} quer{log.queries === 1 ? "y" : "ies"}</p>
            {log.error && <p className="text-red-700">{log.error}</p>}
            <ul className="space-y-0.5">
              {log.retrieved.slice(0, 8).map((p) => (
                <li key={p.chunk_id}><span className="font-mono">{p.section_ref}</span> {p.title} <span className="text-muted-foreground">({p.document}, score {p.score})</span></li>
              ))}
            </ul>
          </div>
        </details>
      ))}
    </div>
  );
}
