import { GeneratedBy, ReqIds, SeverityBadge, StatusPill } from "@/components/common";
import { Evidence } from "@/components/evidence";
import { label } from "@/lib/format";
import type { Finding } from "@/lib/types";

export function FindingCard({ f }: { f: Finding }) {
  return (
    <li className="rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={f.severity} />
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label(f.issue_type)}</span>
        <GeneratedBy value={f.generated_by} />
        {f.resolution && (
          <span title={`Clarification ${f.clarification_refs?.join(", ")}`}>
            <StatusPill status={f.resolution} text={`${label(f.resolution)}${f.clarification_refs?.length ? ` · ${f.clarification_refs.join(", ")}` : ""}`} />
          </span>
        )}
      </div>
      <p className="mt-2 font-medium">{f.title}</p>
      {f.requirement_ids.length > 0 && <div className="mt-2"><ReqIds ids={f.requirement_ids} max={10} /></div>}
      {f.explanation && <p className="mt-2 text-sm text-muted-foreground">{f.explanation}</p>}
      {f.evidence && <p className="mt-2 rounded-md bg-muted px-2 py-1 font-mono text-xs">Evidence: {f.evidence}</p>}
      {f.suggestion && <p className="mt-2 text-sm"><span className="font-medium">Suggested correction: </span>{f.suggestion}</p>}
      {f.question && <p className="mt-1 text-sm text-primary"><span className="font-medium">Question for stakeholders: </span>{f.question}</p>}
      <Evidence citations={f.citations} status={f.evidence_status} />
    </li>
  );
}
