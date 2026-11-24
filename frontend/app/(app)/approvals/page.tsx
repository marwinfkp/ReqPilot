"use client";

import { ArrowRight, CheckCircle2, Flag, MessageSquareWarning, ShieldCheck, XCircle } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth";
import {
  AuthenticityBadge, EmptyState, ErrorState, LoadingState, Notice, PageHeader, ReqIds, Section, SeverityBadge, StatusPill,
} from "@/components/common";
import { ReviewDialog } from "@/components/review-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { ROLE_LABELS, formatDate, label } from "@/lib/format";
import type { Approval, Mapping, RequirementChange, Risk, StakeholderInteraction } from "@/lib/types";

type PendingItem = {
  analysis_id: number;
  project_name: string;
  finished_at: string | null;
  version?: number;
  type: "requirements" | "baseline" | "mappings" | "sdlc" | "workflow" | "requirement_changes"
    | "stakeholder_interactions" | "risks";
  count: number;
  role: string;
  description: string;
  mappings?: Mapping[];
  recommended_model?: string;
  sdlc_status?: string | null;
  changes?: RequirementChange[];
  interviews?: StakeholderInteraction[];
  risks?: Risk[];
};

type HistoryRow = Approval & { analysis_id: number };

const TYPE_ORDER = ["requirements", "baseline", "requirement_changes", "stakeholder_interactions", "mappings", "sdlc",
  "workflow", "risks"];

function compact(value: Record<string, unknown>) {
  const text = Object.entries(value ?? {})
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
  return text.length > 120 ? `${text.slice(0, 117)}…` : text || "–";
}

function MappingRow({ mapping, canReview, onDone }: { mapping: Mapping; canReview: boolean; onDone: () => void }) {
  const review = (action: "approve" | "flag") => (comment: string) =>
    api(`/api/mappings/${mapping.id}/review`, { body: { action, comment } }).then(onDone);
  return (
    <li className="rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant="secondary" className="font-mono text-[11px]">{mapping.requirement_id}</Badge>
        <ArrowRight className="size-3 text-muted-foreground" />
        <span className="font-medium">{mapping.document_name}</span>
        <span className="text-muted-foreground">{mapping.control_ref} {mapping.control_title}</span>
        <AuthenticityBadge value={mapping.authenticity} />
        {mapping.needs_verification && <StatusPill status="warn" text="Needs verification" />}
        {!mapping.citation_verified && <StatusPill status="fail" text="Citation not verified" />}
      </div>
      <blockquote className="mt-2 border-l-2 pl-3 text-xs text-muted-foreground">&ldquo;{mapping.evidence_text}&rdquo;</blockquote>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">Confidence {(mapping.confidence * 100).toFixed(0)}% · {mapping.rationale}</span>
        <div className="flex gap-2">
          <ReviewDialog title="Approve compliance mapping" disabled={!canReview}
            description={`Confirm that ${mapping.document_name} ${mapping.control_ref} supports ${mapping.requirement_id}.`}
            confirmLabel="Approve" onSubmit={review("approve")}
            trigger={<Button size="sm" variant="outline" disabled={!canReview}><CheckCircle2 className="size-4" /> Approve</Button>} />
          <ReviewDialog title="Flag incorrect interpretation" commentRequired disabled={!canReview}
            description="Explain why this regulatory/policy interpretation is incorrect."
            confirmLabel="Flag" onSubmit={review("flag")}
            trigger={<Button size="sm" variant="outline" disabled={!canReview}><Flag className="size-4" /> Flag</Button>} />
        </div>
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------
// Stage 2 (refinement) decisions
// --------------------------------------------------------------------------
function ChangeRow({ change, canApprove, isProposer, onDone }: {
  change: RequirementChange; canApprove: boolean; isProposer: boolean; onDone: () => void;
}) {
  const [approvedText, setApprovedText] = useState(change.proposed_text);
  const disabled = !canApprove || isProposer;
  const review = (action: "approve" | "reject" | "request_changes") => (comment: string) =>
    api(`/api/requirement-changes/${change.id}/review`, {
      body: { action, comment, approved_text: action === "approve" ? approvedText : "" },
    }).then(onDone);
  return (
    <li className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="font-mono text-[11px]">{change.code}</Badge>
        <span className="font-medium">{label(change.change_type)}</span>
        {change.requirement_id
          ? <Badge variant="outline" className="font-mono text-[11px]">{change.requirement_id}</Badge>
          : <Badge variant="outline" className="font-normal">New requirement</Badge>}
        <span className="text-xs text-muted-foreground">proposed by {change.proposed_by} · {formatDate(change.created_at)}</span>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-2">
        <div className="rounded-md bg-muted/60 p-2 text-xs">
          <p className="font-medium text-muted-foreground">Original</p>
          <p>{change.original_text || "–"}</p>
        </div>
        <div className="rounded-md border border-primary/20 bg-primary/5 p-2 text-xs">
          <p className="font-medium text-muted-foreground">Proposed</p>
          <p>{change.change_type === "obsolete" ? "Mark as obsolete" : change.proposed_text}</p>
        </div>
      </div>
      <p className="mt-2 text-xs"><span className="text-muted-foreground">Reason:</span> {change.reason}</p>
      <div className="mt-2 flex flex-wrap items-center justify-end gap-2">
        {isProposer && <span className="text-xs text-muted-foreground">Four-eyes principle: you cannot review your own change.</span>}
        {!canApprove && !isProposer && <span className="text-xs text-muted-foreground">Requires a Business Analyst or Project Manager.</span>}
        <ReviewDialog title={`Approve ${change.code}`} confirmLabel="Approve" disabled={disabled} onSubmit={review("approve")}
          description="The change is applied in the next regeneration, which creates a new analysis version. The current version is not modified."
          trigger={<Button size="sm" disabled={disabled}><CheckCircle2 className="size-4" /> Approve</Button>}>
          {change.change_type !== "obsolete" && (
            <div className="space-y-2">
              <Label htmlFor={`approved-${change.id}`}>Approved text (you may refine it)</Label>
              <Textarea id={`approved-${change.id}`} rows={3} value={approvedText} onChange={(e) => setApprovedText(e.target.value)} />
            </div>
          )}
        </ReviewDialog>
        <ReviewDialog title={`Request changes to ${change.code}`} confirmLabel="Request changes" commentRequired disabled={disabled}
          onSubmit={review("request_changes")}
          trigger={<Button size="sm" variant="outline" disabled={disabled}><MessageSquareWarning className="size-4" /> Request changes</Button>} />
        <ReviewDialog title={`Reject ${change.code}`} confirmLabel="Reject" commentRequired disabled={disabled} onSubmit={review("reject")}
          trigger={<Button size="sm" variant="outline" disabled={disabled}><XCircle className="size-4" /> Reject</Button>} />
      </div>
    </li>
  );
}

function InterviewRow({ interview, canReview, onDone }: { interview: StakeholderInteraction; canReview: boolean; onDone: () => void }) {
  const review = (action: "accept" | "reject" | "request_changes") => (comment: string) =>
    api(`/api/stakeholder-interactions/${interview.id}/review`, { body: { action, comment } }).then(onDone);
  return (
    <li className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="font-mono text-[11px]">SI-{String(interview.id).padStart(2, "0")}</Badge>
        <span className="font-medium">{interview.stakeholder_role}</span>
        {interview.interviewee && <span className="text-muted-foreground">({interview.interviewee})</span>}
        <span className="text-xs text-muted-foreground">recorded by {interview.recorded_by} · {formatDate(interview.created_at)}</span>
      </div>
      {interview.topic && <p className="mt-1 text-xs text-muted-foreground">Topic: {interview.topic}</p>}
      {interview.requirement_ids.length > 0 && <div className="mt-1"><ReqIds ids={interview.requirement_ids} /></div>}
      <ul className="mt-2 space-y-1 text-xs">
        {interview.entries.map((e, i) => (
          <li key={i}><span className="font-medium">Q:</span> {e.question} <span className="font-medium">A:</span> {e.answer || "–"}</li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap justify-end gap-2">
        {!canReview && <span className="self-center text-xs text-muted-foreground">Requires a Business Analyst or Project Manager.</span>}
        <ReviewDialog title="Accept interview" confirmLabel="Accept" disabled={!canReview} onSubmit={review("accept")}
          description="Accepted answers are applied as supplementary information in the next regeneration."
          trigger={<Button size="sm" disabled={!canReview}><CheckCircle2 className="size-4" /> Accept</Button>} />
        <ReviewDialog title="Request changes" confirmLabel="Request changes" commentRequired disabled={!canReview}
          onSubmit={review("request_changes")}
          trigger={<Button size="sm" variant="outline" disabled={!canReview}>Request changes</Button>} />
        <ReviewDialog title="Reject interview" confirmLabel="Reject" commentRequired disabled={!canReview} onSubmit={review("reject")}
          trigger={<Button size="sm" variant="outline" disabled={!canReview}>Reject</Button>} />
      </div>
    </li>
  );
}

function RiskRow({ risk, canAccept, onDone }: { risk: Risk; canAccept: boolean; onDone: () => void }) {
  const review = (action: "accept" | "reject") => (comment: string) =>
    api(`/api/risks/${risk.id}/review`, { body: { action, comment } }).then(onDone);
  return (
    <li className="flex flex-col gap-2 rounded-md border p-3 text-sm md:flex-row md:items-center md:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary" className="font-mono text-[11px]">{risk.risk_id}</Badge>
          <SeverityBadge severity={risk.severity} />
          <span className="text-xs text-muted-foreground">{label(risk.category)} · score {risk.score}</span>
        </div>
        <p className="mt-1">{risk.description}</p>
        {risk.category === "security" && (
          <p className="text-xs text-muted-foreground">Security risks need a Security Officer or Administrator.</p>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
        <ReviewDialog title={`Accept ${risk.risk_id}`} confirmLabel="Accept risk" commentRequired disabled={!canAccept}
          onSubmit={review("accept")}
          description="Record that this risk is knowingly accepted (with its mitigation). The decision is auditable."
          trigger={<Button size="sm" disabled={!canAccept}>Accept</Button>} />
        <ReviewDialog title={`Reject ${risk.risk_id}`} confirmLabel="Reject" commentRequired disabled={!canAccept}
          onSubmit={review("reject")}
          description="Rejecting means the risk is not acceptable and must be mitigated before go-live."
          trigger={<Button size="sm" variant="outline" disabled={!canAccept}>Reject</Button>} />
      </div>
    </li>
  );
}

function PendingActions({ item, can, reload }: { item: PendingItem; can: (p: string) => boolean; reload: () => void }) {
  const id = item.analysis_id;
  if (item.type === "sdlc") {
    const allowed = can("sdlc:approve");
    return (
      <div className="flex flex-wrap gap-2">
        <ReviewDialog title="Approve SDLC recommendation" disabled={!allowed} confirmLabel="Approve"
          description={`Adopt ${item.recommended_model} for this project. To override with another model, use the SDLC Recommendation page.`}
          onSubmit={(comment) => api(`/api/analyses/${id}/sdlc/review`, { body: { action: "approve", comment } }).then(reload)}
          trigger={<Button size="sm" disabled={!allowed}><CheckCircle2 className="size-4" /> Approve</Button>} />
        <Button size="sm" variant="outline" asChild><Link href={`/analyses/${id}/sdlc`}>Review / override</Link></Button>
      </div>
    );
  }
  if (item.type === "workflow") {
    const allowed = can("workflow:approve");
    const blocked = item.sdlc_status === "pending_approval";
    return (
      <div className="flex flex-wrap items-center gap-2">
        {blocked && <span className="text-xs text-muted-foreground">Decide the SDLC recommendation first</span>}
        <ReviewDialog title="Approve SDLC workflow" disabled={!allowed || blocked} confirmLabel="Approve"
          onSubmit={(comment) => api(`/api/analyses/${id}/workflow/review`, { body: { action: "approve", comment } }).then(reload)}
          trigger={<Button size="sm" disabled={!allowed || blocked}><CheckCircle2 className="size-4" /> Approve</Button>} />
        <Button size="sm" variant="outline" asChild><Link href={`/analyses/${id}/workflow`}>View workflow</Link></Button>
      </div>
    );
  }
  if (item.type === "baseline") {
    const allowed = can("baseline:approve");
    return (
      <ReviewDialog title="Approve requirement baseline" disabled={!allowed} confirmLabel="Approve baseline"
        description="All requirements must be reviewed first. Rejected requirements are excluded from the baseline; later changes reopen it."
        onSubmit={(comment) => api(`/api/analyses/${id}/baseline`, { body: { comment } }).then(reload)}
        trigger={<Button size="sm" disabled={!allowed}><ShieldCheck className="size-4" /> Approve baseline</Button>} />
    );
  }
  if (item.type === "requirements") {
    const allowed = can("requirement:review");
    return (
      <div className="flex flex-wrap gap-2">
        <ReviewDialog title="Accept all pending requirements" disabled={!allowed} confirmLabel="Accept all"
          description={`You are accepting ${item.count} requirement(s) as a human reviewer. Each acceptance is recorded in the approval history.`}
          onSubmit={(comment) => api(`/api/analyses/${id}/requirements/accept-pending`, { body: { comment } }).then(reload)}
          trigger={<Button size="sm" disabled={!allowed}><CheckCircle2 className="size-4" /> Accept all pending</Button>} />
        <Button size="sm" variant="outline" asChild><Link href={`/analyses/${id}/requirements`}>Review individually</Link></Button>
      </div>
    );
  }
  return null;
}

export default function ApprovalsPage() {
  const { can, user } = useAuth();
  const [items, setItems] = useState<PendingItem[] | null>(null);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      api<{ items: PendingItem[] }>("/api/approvals/pending"),
      api<HistoryRow[]>("/api/approvals?limit=200"),
    ])
      .then(([pending, rows]) => {
        setItems(pending.items);
        setHistory(rows);
      })
      .catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const groups = useMemo(() => {
    const byAnalysis = new Map<number, PendingItem[]>();
    for (const item of items ?? []) byAnalysis.set(item.analysis_id, [...(byAnalysis.get(item.analysis_id) ?? []), item]);
    return [...byAnalysis.entries()].map(([analysisId, list]) => ({
      analysisId,
      project: list[0].project_name,
      version: list[0].version ?? 1,
      finished: list[0].finished_at,
      items: list.sort((a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type)),
    }));
  }, [items]);

  return (
    <>
      <PageHeader title="Approval Centre"
        description="Human-in-the-loop decisions taken after the automatic analysis: requirement baselines, regulatory interpretations, SDLC adoption and workflow approval." />
      <div className="mb-6">
        <Notice>
          ReqPilot&apos;s AI agents never approve anything themselves. Every decision below is made by an authorised person and recorded
          with reviewer, role, timestamp, comment and the previous and new values (also written to the audit log).
        </Notice>
      </div>

      {error ? <ErrorState message={error} retry={load} /> : !items ? <LoadingState /> : (
        <div className="space-y-6">
          {groups.length === 0 ? (
            <EmptyState icon={CheckCircle2} title="Nothing awaits approval" description="All completed analyses have been reviewed." />
          ) : groups.map((group) => (
            <Section key={group.analysisId}
              title={<span>{group.project}{group.version > 1 && <Badge variant="outline" className="ml-2 font-normal">v{group.version}</Badge>}{" "}
                <span className="font-normal text-muted-foreground">· analysis #{group.analysisId}</span></span>}
              description={`Completed ${formatDate(group.finished)}`}
              actions={<Button variant="outline" size="sm" asChild><Link href={`/analyses/${group.analysisId}`}>Results <ArrowRight className="size-4" /></Link></Button>}>
              <ul className="space-y-4">
                {group.items.map((item) => (
                  <li key={item.type} className="rounded-lg border bg-muted/20 p-4">
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                      <div>
                        <p className="font-medium">{item.description}</p>
                        <p className="text-xs text-muted-foreground">Required role: {item.role}</p>
                      </div>
                      <PendingActions item={item} can={can} reload={load} />
                    </div>
                    {item.type === "mappings" && item.mappings && (
                      <>
                        {!can("mapping:review") && (
                          <p className="mt-3 text-xs text-muted-foreground">Only a Compliance Officer (or Administrator) can approve or flag mappings.</p>
                        )}
                        <ul className="mt-3 space-y-2">
                          {item.mappings.map((m) => (
                            <MappingRow key={m.id} mapping={m} canReview={can("mapping:review")} onDone={load} />
                          ))}
                        </ul>
                        {item.count > item.mappings.length && (
                          <p className="mt-2 text-xs text-muted-foreground">
                            Showing {item.mappings.length} of {item.count}.{" "}
                            <Link className="underline" href={`/analyses/${item.analysis_id}/compliance`}>Open the Compliance Centre</Link> for all.
                          </p>
                        )}
                      </>
                    )}
                    {item.type === "requirement_changes" && item.changes && (
                      <ul className="mt-3 space-y-2">
                        {item.changes.map((c) => (
                          <ChangeRow key={c.id} change={c} canApprove={can("change:approve")}
                            isProposer={c.proposed_by === user?.email} onDone={load} />
                        ))}
                      </ul>
                    )}
                    {item.type === "stakeholder_interactions" && item.interviews && (
                      <ul className="mt-3 space-y-2">
                        {item.interviews.map((i) => (
                          <InterviewRow key={i.id} interview={i} canReview={can("stakeholder:review")} onDone={load} />
                        ))}
                      </ul>
                    )}
                    {item.type === "risks" && item.risks && (
                      <>
                        {!can("risk:accept") && (
                          <p className="mt-3 text-xs text-muted-foreground">Only a Project Manager, Security Officer or Administrator can accept risks.</p>
                        )}
                        <ul className="mt-3 space-y-2">
                          {item.risks.map((r) => (
                            <RiskRow key={r.id} risk={r} canAccept={can("risk:accept")} onDone={load} />
                          ))}
                        </ul>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
          ))}

          <Section title="Approval history" description="Most recent human decisions across all analyses.">
            {!history?.length ? <p className="text-sm text-muted-foreground">No decisions recorded yet.</p> : (
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>When</TableHead>
                    <TableHead>Reviewer</TableHead>
                    <TableHead>Analysis</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Comment</TableHead>
                    <TableHead>Previous → new</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.map((h) => (
                    <TableRow key={h.id}>
                      <TableCell className="whitespace-nowrap text-xs">{formatDate(h.timestamp)}</TableCell>
                      <TableCell className="text-xs">
                        <p className="font-medium">{h.reviewer_email}</p>
                        <p className="text-muted-foreground">{ROLE_LABELS[h.reviewer_role] ?? h.reviewer_role}</p>
                      </TableCell>
                      <TableCell><Link className="text-xs underline" href={`/analyses/${h.analysis_id}`}>#{h.analysis_id}</Link></TableCell>
                      <TableCell className="text-xs">{label(h.target_type)} <span className="font-mono">{h.target_id}</span></TableCell>
                      <TableCell><StatusPill status={h.action === "approve" || h.action === "accept" ? "approved" : h.action === "reject" ? "rejected" : h.action === "flag" ? "flagged" : "modified"} text={label(h.action)} /></TableCell>
                      <TableCell className="max-w-[220px] text-xs">{h.comment || "–"}</TableCell>
                      <TableCell className="max-w-[320px] text-xs text-muted-foreground">
                        {compact(h.previous_value)} <span className="text-foreground">→</span> {compact(h.new_value)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              </div>
            )}
          </Section>
        </div>
      )}
    </>
  );
}
