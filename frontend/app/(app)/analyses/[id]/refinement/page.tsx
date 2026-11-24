"use client";

// Stage 2 - Refinement Centre (optional). Opening this page is the explicit user action
// that starts Stage 2. Nothing here changes the current analysis version: approved
// inputs are applied by a dependency-aware regeneration that creates a NEW version.
import {
  ArrowRight, CheckCircle2, FilePlus2, GitCompare, History, Loader2, MessageCircleQuestion, Plus, RefreshCcw,
  Sparkles, Trash2, Users, Wrench,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { useLoadedAnalysis } from "@/components/analysis-context";
import { ClarificationCentre } from "@/components/clarification-centre";
import {
  EmptyState, ErrorState, LoadingState, Notice, ReqIds, Section, SeverityBadge, StatTile, StatusPill,
} from "@/components/common";
import { FindingCard } from "@/components/finding-card";
import { ReviewDialog } from "@/components/review-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { ROLE_LABELS, formatDate, label } from "@/lib/format";
import type {
  AgentRun, AnalysisBrief, Approval, ClarificationQuestion, Finding, QuestionCounts, RegenerationPlan,
  RegenerationRunInfo, RequirementChange, Risk, StakeholderInteraction,
} from "@/lib/types";

type Overview = {
  analysis: {
    id: number; version: number; status: string; stage1_status: string; stage2_status: string; is_latest: boolean;
    latest_id: number; latest_version: number; project_name: string;
  };
  outstanding: { conflicts: Finding[]; ambiguities: Finding[]; incomplete: Finding[]; compliance_gaps: Finding[]; security: Finding[] };
  high_risks: Risk[];
  questions: ClarificationQuestion[];
  question_counts: QuestionCounts;
  changes: RequirementChange[];
  interviews: StakeholderInteraction[];
  regenerations: RegenerationRunInfo[];
  pending_inputs: { changes: number; questions: number; interviews: number };
  suggested_areas: { area: string; count: number }[];
  log: AgentRun[];
};

type Suggestion = { role: string; questions: string[]; requirement_ids: string[]; reasons: string[] };

type Comparison = {
  base: { id: number; version: number; status: string };
  target: { id: number; version: number; status: string };
  requirements: {
    added: { req_id: string; statement: string }[];
    removed: string[];
    changed: { req_id: string; before: string; after: string; status: string; changes?: string[]; supplementary_added?: string[] }[];
    recategorised: { req_id: string; before: string[]; after: string[] }[];
  };
  metrics: { metric: string; before: number | string | null; after: number | string | null }[];
  sdlc: {
    before: { model: string; score: number } | null;
    after: { model: string; score: number } | null;
    factors_changed: { factor: string; before: number; after: number }[];
  };
  workflow: {
    before: { model: string; phases: number } | null;
    after: { model: string; phases: number } | null;
    customisations_added: string[];
    customisations_removed: string[];
  };
  validation: { before: Record<string, string>; after: Record<string, string> };
};

type ChangeDraft = { requirement_id: string; change_type: RequirementChange["change_type"] };

const CHANGE_TYPES: [RequirementChange["change_type"], string][] = [
  ["edit", "Edit wording"],
  ["acceptance_criteria", "Add acceptance criteria"],
  ["new", "Add new requirement"],
  ["resolve_conflict", "Resolve contradictory values"],
  ["business_rule", "Update business rule"],
  ["constraint", "Add security / compliance constraint"],
  ["obsolete", "Mark obsolete"],
];
const CHANGE_LABEL = Object.fromEntries(CHANGE_TYPES);

const STAKEHOLDER_ROLES = ["Project Manager", "Product Owner", "Business Analyst", "Compliance Officer", "Security Team",
  "Operations Team", "Development Team", "Quality Assurance Team", "Data Protection Officer", "System Architect",
  "Risk Management", "Customer"];

const DONE = ["completed", "completed_with_warnings"];

function ApprovalStatus({ status }: { status: string }) {
  const mapped = status === "accepted" ? "approved" : status;
  return <StatusPill status={mapped} text={label(status)} />;
}

function compact(value: Record<string, unknown>) {
  const text = Object.entries(value ?? {})
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
  return text.length > 140 ? `${text.slice(0, 137)}…` : text || "–";
}

// ==========================================================================
// a. Overview
// ==========================================================================
function OverviewTab({ data }: { data: Overview }) {
  const { analysis: a, outstanding: o, question_counts: q, pending_inputs: p } = data;
  const pending = p.changes + p.questions + p.interviews;
  return (
    <div className="space-y-6">
      <Notice>
        Stage 2 is <strong>optional</strong>. Your Stage 1 results (requirements, compliance, risks, SDLC recommendation and
        workflow) are already complete and stay unchanged. Refinement collects further clarification, stakeholder interviews and
        approved requirement changes; a confirmed regeneration then re-runs only the affected agents and stores the result as
        a <strong>new version</strong> - this version is always preserved.
      </Notice>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Analysis version" value={`v${a.version}`} hint={a.is_latest ? "latest version" : `latest is v${a.latest_version}`} icon={History} />
        <StatTile label="Stage 1" value={<StatusPill status={a.stage1_status} />} hint="12-agent analysis" />
        <StatTile label="Stage 2" value={<StatusPill status={a.stage2_status} />} hint="optional refinement" />
        <StatTile label="Inputs ready to apply" value={pending} hint={`${p.changes} change(s) · ${p.questions} answer(s) · ${p.interviews} interview(s)`}
          icon={RefreshCcw} tone={pending ? "warning" : "default"} />
      </div>
      <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Open conflicts" value={o.conflicts.length} tone={o.conflicts.length ? "danger" : "good"} />
        <StatTile label="Open ambiguities" value={o.ambiguities.length} tone={o.ambiguities.length ? "warning" : "good"} />
        <StatTile label="Incomplete" value={o.incomplete.length} tone={o.incomplete.length ? "warning" : "good"} />
        <StatTile label="Compliance gaps" value={o.compliance_gaps.length} tone={o.compliance_gaps.length ? "danger" : "good"} />
        <StatTile label="High security findings" value={o.security.length} tone={o.security.length ? "danger" : "good"} />
        <StatTile label="High / critical risks" value={data.high_risks.length} tone={data.high_risks.length ? "danger" : "good"} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Areas that could benefit from clarification" description="Derived from findings still open in this version.">
          {data.suggested_areas.length ? (
            <ul className="space-y-2">
              {data.suggested_areas.map((s) => (
                <li key={s.area} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                  <span>{s.area}</span><Badge variant="secondary">{s.count}</Badge>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No open areas" description="Every issue in this version is resolved." />}
        </Section>
        <Section title="Clarification questions" description="Stage 1 questions plus any Stage 2 follow-ups.">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            {([["Total", q.total], ["Answered", q.answered], ["Deferred", q.deferred], ["Need stakeholder", q.needs_stakeholder],
              ["Unanswered", q.open], ["Critical unanswered", q.critical_unanswered]] as [string, number][]).map(([k, v]) => (
              <div key={k} className="rounded-md border p-3"><p className="text-xs text-muted-foreground">{k}</p><p className="text-lg font-semibold tabular-nums">{v}</p></div>
            ))}
          </div>
        </Section>
      </div>
      <Section title="Stage 2 activity log" description="Every refinement operation recorded for this version (human and agent actions).">
        {data.log.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow><TableHead>Operation</TableHead><TableHead>Status</TableHead><TableHead>Mode</TableHead><TableHead className="text-right">Time</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.log.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="whitespace-nowrap font-medium">{r.agent}</TableCell>
                    <TableCell><StatusPill status={r.status} /></TableCell>
                    <TableCell className="text-xs">{r.mode === "human" ? "Human action" : r.mode === "rules" ? "Rules only" : "OpenAI + rules"}</TableCell>
                    <TableCell className="text-right text-xs tabular-nums">{r.duration_ms} ms</TableCell>
                    <TableCell className="min-w-[320px] whitespace-normal text-xs text-muted-foreground">{r.message}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <p className="text-sm text-muted-foreground">No Stage 2 activity yet.</p>}
      </Section>
    </div>
  );
}

// ==========================================================================
// b. Outstanding issues
// ==========================================================================
function IssuesTab({ data, onAsk, onPropose, readOnly }: {
  data: Overview; onAsk: () => void; onPropose: (draft: ChangeDraft) => void; readOnly: boolean;
}) {
  const groups: [string, Finding[], RequirementChange["change_type"]][] = [
    ["Requirement conflicts", data.outstanding.conflicts, "resolve_conflict"],
    ["Ambiguities", data.outstanding.ambiguities, "edit"],
    ["Incomplete requirements", data.outstanding.incomplete, "acceptance_criteria"],
    ["Compliance gaps", data.outstanding.compliance_gaps, "constraint"],
    ["High security findings", data.outstanding.security, "constraint"],
  ];
  return (
    <div className="space-y-6">
      {groups.map(([title, findings, type]) => (
        <Section key={title} title={`${title} (${findings.length})`}>
          {findings.length ? (
            <div className="grid gap-3 xl:grid-cols-2">
              {findings.map((f) => (
                <div key={f.id} className="space-y-2">
                  <ul><FindingCard f={f} /></ul>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button size="sm" variant="outline" disabled={readOnly} onClick={onAsk}>
                      <MessageCircleQuestion className="size-4" /> Ask a question
                    </Button>
                    <Button size="sm" variant="outline" disabled={readOnly}
                      onClick={() => onPropose({ requirement_id: f.requirement_ids[0] ?? "", change_type: f.requirement_ids.length ? type : "new" })}>
                      <Wrench className="size-4" /> Propose change
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">Nothing outstanding.</p>}
        </Section>
      ))}
      <Section title={`High / critical risks (${data.high_risks.length})`} description="Formal risk acceptance is recorded in the Approval Centre.">
        {data.high_risks.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Risk</TableHead><TableHead>Category</TableHead><TableHead>Severity</TableHead><TableHead className="text-right">Score</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
              <TableBody>
                {data.high_risks.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-mono text-xs">{r.risk_id}</TableCell>
                    <TableCell className="min-w-[260px] whitespace-normal text-sm">{r.description}</TableCell>
                    <TableCell className="text-xs">{label(r.category)}</TableCell>
                    <TableCell><SeverityBadge severity={r.severity} /></TableCell>
                    <TableCell className="text-right tabular-nums">{r.score}</TableCell>
                    <TableCell><ApprovalStatus status={r.status} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <p className="text-sm text-muted-foreground">No high or critical risks.</p>}
      </Section>
    </div>
  );
}

// ==========================================================================
// c. Clarification
// ==========================================================================
function ClarificationTab({ analysisId, readOnly, onChanged }: { analysisId: number; readOnly: boolean; onChanged: () => void }) {
  const { can } = useAuth();
  const [key, setKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const filter = useCallback((q: ClarificationQuestion) => q.stage === 2 || q.status !== "answered", []);
  const generate = async () => {
    setBusy(true);
    try {
      const result = await api<{ message: string; questions: ClarificationQuestion[] }>(
        `/api/analyses/${analysisId}/refinement/questions`, { method: "POST" });
      toast.success(result.message);
      setKey((k) => k + 1);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <ClarificationCentre analysisId={analysisId} stage={2} filter={filter} refreshKey={key}
      header={
        <Card>
          <CardContent className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="space-y-1 text-sm">
              <p className="font-medium">Advanced clarification</p>
              <p className="text-muted-foreground">
                Shows Stage 2 follow-up questions and Stage 1 questions that are still unanswered. Answers are stored with the
                responding user and time, and are applied by the next regeneration (they never change this version).
              </p>
            </div>
            <Button disabled={busy || readOnly || !can("clarification:answer")} onClick={generate}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />} Generate more questions
            </Button>
          </CardContent>
        </Card>
      } />
  );
}

// ==========================================================================
// d. Stakeholder interaction
// ==========================================================================
type InterviewForm = { role: string; interviewee: string; topic: string; ids: string; entries: { question: string; answer: string }[] };
const EMPTY_INTERVIEW: InterviewForm = { role: "", interviewee: "", topic: "", ids: "", entries: [{ question: "", answer: "" }] };

function StakeholdersTab({ analysisId, data, readOnly, requirementIds, reload }: {
  analysisId: number; data: Overview; readOnly: boolean; requirementIds: string[]; reload: () => void;
}) {
  const { can } = useAuth();
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const [form, setForm] = useState<InterviewForm | null>(null);
  const [saving, setSaving] = useState(false);

  const loadSuggestions = useCallback(() => {
    setSuggestError(null);
    api<Suggestion[]>(`/api/analyses/${analysisId}/stakeholders/suggestions`).then(setSuggestions)
      .catch((e) => setSuggestError(errorMessage(e)));
  }, [analysisId]);
  useEffect(loadSuggestions, [loadSuggestions]);

  const known = useMemo(() => new Set(requirementIds), [requirementIds]);
  const parsedIds = (form?.ids ?? "").split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  const unknownIds = parsedIds.filter((id) => !known.has(id));

  const submit = async () => {
    if (!form) return;
    if (!form.role.trim()) return toast.error("Choose the stakeholder role");
    if (unknownIds.length) return toast.error(`Unknown requirement IDs: ${unknownIds.join(", ")}`);
    const entries = form.entries.filter((e) => e.question.trim());
    if (!entries.length || !entries.some((e) => e.answer.trim())) return toast.error("Record at least one question with an answer");
    setSaving(true);
    try {
      await api(`/api/analyses/${analysisId}/stakeholder-interactions`, {
        body: { stakeholder_role: form.role.trim(), interviewee: form.interviewee.trim(), topic: form.topic.trim(),
          requirement_ids: parsedIds, entries },
      });
      toast.success("Interview recorded - awaiting review");
      setForm(null);
      reload();
      loadSuggestions();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const review = (id: number, action: "accept" | "reject" | "request_changes") => async (comment: string) => {
    await api(`/api/stakeholder-interactions/${id}/review`, { body: { action, comment } });
    reload();
  };

  const canRecord = can("stakeholder:record") && !readOnly;
  const canReview = can("stakeholder:review") && !readOnly;

  return (
    <div className="space-y-6">
      <Notice tone="warning">
        ReqPilot does <strong>not</strong> contact stakeholders. Conduct the interview yourself (meeting, call or e-mail) and record
        the answers here. Each interview is stored with your name, role and time and must be reviewed before it is applied.
      </Notice>
      <Section title="Suggested interviews" description="Stakeholder roles best placed to resolve the issues still open."
        actions={<Button size="sm" disabled={!canRecord} onClick={() => setForm({ ...EMPTY_INTERVIEW })}><Plus className="size-4" /> New interview</Button>}>
        {suggestError ? <ErrorState message={suggestError} retry={loadSuggestions} /> : !suggestions ? <LoadingState /> : suggestions.length === 0 ? (
          <EmptyState icon={Users} title="No suggested interviews" description="No open issues need stakeholder input right now." />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {suggestions.map((s) => (
              <div key={s.role} className="flex flex-col gap-2 rounded-lg border p-4 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <p className="font-medium">{s.role}</p>
                  <Badge variant="secondary">{s.questions.length} question(s)</Badge>
                </div>
                {s.requirement_ids.length > 0 && <ReqIds ids={s.requirement_ids} max={5} />}
                {s.reasons.length > 0 && <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">{s.reasons.slice(0, 3).map((r) => <li key={r}>{r}</li>)}</ul>}
                <ul className="space-y-1 text-xs">{s.questions.slice(0, 3).map((q) => <li key={q} className="rounded bg-muted px-2 py-1">{q}</li>)}</ul>
                <Button size="sm" variant="outline" className="mt-auto" disabled={!canRecord}
                  onClick={() => setForm({ role: s.role, interviewee: "", topic: s.reasons[0] ?? "", ids: s.requirement_ids.join(", "),
                    entries: s.questions.map((q) => ({ question: q, answer: "" })) })}>
                  Start interview <ArrowRight className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title={`Recorded interviews (${data.interviews.length})`} description="Accepted interviews are applied as supplementary information in the next regeneration.">
        {data.interviews.length === 0 ? <p className="text-sm text-muted-foreground">No interviews recorded yet.</p> : (
          <ul className="space-y-3">
            {data.interviews.map((i) => (
              <li key={i.id} className="rounded-lg border p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary" className="font-mono text-[11px]">SI-{String(i.id).padStart(2, "0")}</Badge>
                  <span className="font-medium">{i.stakeholder_role}</span>
                  {i.interviewee && <span className="text-muted-foreground">({i.interviewee})</span>}
                  <ApprovalStatus status={i.status} />
                  {i.applied_in_version && <Badge variant="outline" className="font-normal">Applied in v{i.applied_in_version}</Badge>}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Recorded by {i.recorded_by} ({ROLE_LABELS[i.recorder_role] ?? i.recorder_role}) · {formatDate(i.created_at)}
                  {i.reviewed_by && ` · reviewed by ${i.reviewed_by}`}{i.review_comment && ` - "${i.review_comment}"`}
                </p>
                {i.topic && <p className="mt-1 text-xs">Topic: {i.topic}</p>}
                {i.requirement_ids.length > 0 && <div className="mt-1"><ReqIds ids={i.requirement_ids} /></div>}
                <ul className="mt-2 space-y-1 text-xs">
                  {i.entries.map((e, n) => <li key={n}><span className="font-medium">Q:</span> {e.question} <span className="font-medium">A:</span> {e.answer || "–"}</li>)}
                </ul>
                {i.status === "pending_review" && (
                  <div className="mt-3 flex flex-wrap justify-end gap-2">
                    {!canReview && <span className="self-center text-xs text-muted-foreground">Review requires a Business Analyst or Project Manager.</span>}
                    <ReviewDialog title="Accept interview" confirmLabel="Accept" disabled={!canReview} onSubmit={review(i.id, "accept")}
                      trigger={<Button size="sm" disabled={!canReview}><CheckCircle2 className="size-4" /> Accept</Button>} />
                    <ReviewDialog title="Request changes" confirmLabel="Request changes" commentRequired disabled={!canReview}
                      onSubmit={review(i.id, "request_changes")} trigger={<Button size="sm" variant="outline" disabled={!canReview}>Request changes</Button>} />
                    <ReviewDialog title="Reject interview" confirmLabel="Reject" commentRequired disabled={!canReview}
                      onSubmit={review(i.id, "reject")} trigger={<Button size="sm" variant="outline" disabled={!canReview}>Reject</Button>} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Dialog open={!!form} onOpenChange={(o) => !o && setForm(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Record stakeholder interview</DialogTitle>
            <DialogDescription>Record the questions you asked and the answers you received. Nothing is sent to anyone.</DialogDescription>
          </DialogHeader>
          {form && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>Stakeholder role</Label>
                  <Select value={STAKEHOLDER_ROLES.includes(form.role) ? form.role : "__custom"}
                    onValueChange={(v) => setForm({ ...form, role: v === "__custom" ? "" : v })}>
                    <SelectTrigger className="w-full"><SelectValue placeholder="Choose a role" /></SelectTrigger>
                    <SelectContent>
                      {STAKEHOLDER_ROLES.map((r) => <SelectItem key={r} value={r}>{r}</SelectItem>)}
                      <SelectItem value="__custom">Other (type below)</SelectItem>
                    </SelectContent>
                  </Select>
                  {!STAKEHOLDER_ROLES.includes(form.role) && (
                    <Input placeholder="Role" value={form.role} maxLength={60} onChange={(e) => setForm({ ...form, role: e.target.value })} />
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="interviewee">Interviewee name (optional)</Label>
                  <Input id="interviewee" value={form.interviewee} maxLength={200} onChange={(e) => setForm({ ...form, interviewee: e.target.value })} />
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="topic">Topic</Label>
                <Input id="topic" value={form.topic} maxLength={300} onChange={(e) => setForm({ ...form, topic: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="ids">Related requirement IDs (comma-separated)</Label>
                <Input id="ids" list="requirement-ids" value={form.ids} placeholder="e.g. FR-BNK-001, NFR-BNK-004"
                  onChange={(e) => setForm({ ...form, ids: e.target.value })} />
                <datalist id="requirement-ids">{requirementIds.map((id) => <option key={id} value={id} />)}</datalist>
                {unknownIds.length > 0 && <p className="text-xs text-red-700">Not in this version: {unknownIds.join(", ")}</p>}
              </div>
              <div className="space-y-3">
                <Label>Questions and answers</Label>
                {form.entries.map((entry, n) => (
                  <div key={n} className="space-y-2 rounded-md border p-3">
                    <div className="flex items-start gap-2">
                      <Textarea rows={2} placeholder="Question asked" value={entry.question}
                        onChange={(e) => setForm({ ...form, entries: form.entries.map((x, i) => (i === n ? { ...x, question: e.target.value } : x)) })} />
                      <Button size="icon" variant="ghost" aria-label="Remove question" disabled={form.entries.length === 1}
                        onClick={() => setForm({ ...form, entries: form.entries.filter((_, i) => i !== n) })}><Trash2 className="size-4" /></Button>
                    </div>
                    <Textarea rows={2} placeholder="Answer given by the stakeholder" value={entry.answer}
                      onChange={(e) => setForm({ ...form, entries: form.entries.map((x, i) => (i === n ? { ...x, answer: e.target.value } : x)) })} />
                  </div>
                ))}
                <Button size="sm" variant="outline" onClick={() => setForm({ ...form, entries: [...form.entries, { question: "", answer: "" }] })}>
                  <Plus className="size-4" /> Add question
                </Button>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setForm(null)}>Cancel</Button>
            <Button disabled={saving} onClick={submit}>{saving && <Loader2 className="size-4 animate-spin" />} Record interview</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ==========================================================================
// e. Requirement changes
// ==========================================================================
function ChangeReviewActions({ change, reload, readOnly }: { change: RequirementChange; reload: () => void; readOnly: boolean }) {
  const { can, user } = useAuth();
  const [approvedText, setApprovedText] = useState(change.proposed_text);
  const isProposer = change.proposed_by === user?.email;
  const disabled = readOnly || isProposer || !can("change:approve");
  const review = (action: "approve" | "reject" | "request_changes") => async (comment: string) => {
    await api(`/api/requirement-changes/${change.id}/review`, {
      body: { action, comment, approved_text: action === "approve" ? approvedText : "" },
    });
    reload();
  };
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex flex-wrap justify-end gap-1">
        <ReviewDialog title={`Approve ${change.code}`} confirmLabel="Approve" disabled={disabled} onSubmit={review("approve")}
          description="Approved changes are applied by the next regeneration in a new version; the original requirement is kept."
          trigger={<Button size="sm" disabled={disabled}>Approve</Button>}>
          {change.change_type !== "obsolete" && (
            <div className="space-y-2">
              <Label htmlFor={`approved-${change.id}`}>Approved text (optional edit)</Label>
              <Textarea id={`approved-${change.id}`} rows={3} value={approvedText} onChange={(e) => setApprovedText(e.target.value)} />
            </div>
          )}
        </ReviewDialog>
        <ReviewDialog title={`Request changes to ${change.code}`} confirmLabel="Request changes" commentRequired disabled={disabled}
          onSubmit={review("request_changes")} trigger={<Button size="sm" variant="outline" disabled={disabled}>Changes</Button>} />
        <ReviewDialog title={`Reject ${change.code}`} confirmLabel="Reject" commentRequired disabled={disabled}
          onSubmit={review("reject")} trigger={<Button size="sm" variant="outline" disabled={disabled}>Reject</Button>} />
      </div>
      {isProposer && <span className="text-[11px] text-muted-foreground">Four-eyes: you proposed this change</span>}
    </div>
  );
}

function ResubmitAction({ change, reload }: { change: RequirementChange; reload: () => void }) {
  const [text, setText] = useState(change.proposed_text);
  const [reason, setReason] = useState(change.reason);
  return (
    <ReviewDialog title={`Resubmit ${change.code}`} confirmLabel="Submit for review"
      description={change.review_comment ? `Reviewer comment: ${change.review_comment}` : "Submit this draft for review."}
      onSubmit={async () => {
        await api(`/api/requirement-changes/${change.id}/submit`, { body: { proposed_text: text, reason } });
        reload();
      }}
      trigger={<Button size="sm" variant="outline">Resubmit</Button>}>
      <div className="space-y-3">
        {change.change_type !== "obsolete" && (
          <div className="space-y-2"><Label>Proposed text</Label><Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} /></div>
        )}
        <div className="space-y-2"><Label>Reason</Label><Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
    </ReviewDialog>
  );
}

function ChangesTab({ analysisId, data, draft, readOnly, reload }: {
  analysisId: number; data: Overview; draft: ChangeDraft | null; readOnly: boolean; reload: () => void;
}) {
  const { can, user } = useAuth();
  const { analysis } = useLoadedAnalysis();
  const requirements = analysis.requirements.filter((r) => r.status !== "obsolete");
  const [type, setType] = useState<RequirementChange["change_type"]>("edit");
  const [reqId, setReqId] = useState("");
  const [text, setText] = useState("");
  const [kind, setKind] = useState("functional");
  const [reason, setReason] = useState("");
  const [source, setSource] = useState("user");
  const [asDraft, setAsDraft] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (draft) {
      setType(draft.change_type);
      setReqId(draft.requirement_id);
    }
  }, [draft]);

  const current = requirements.find((r) => r.req_id === reqId);
  const canPropose = can("change:propose") && !readOnly;

  const submit = async () => {
    if (type !== "new" && !reqId) return toast.error("Choose the requirement to change");
    if (type !== "obsolete" && !text.trim()) return toast.error("Enter the proposed text");
    if (reason.trim().length < 3) return toast.error("Explain the reason for the change");
    setSaving(true);
    try {
      const change = await api<RequirementChange>(`/api/analyses/${analysisId}/requirement-changes`, {
        body: { change_type: type, requirement_id: type === "new" ? "" : reqId, proposed_text: text,
          new_kind: type === "new" ? kind : "", reason, source: source || "user", submit: !asDraft },
      });
      toast.success(`${change.code} ${asDraft ? "saved as draft" : "submitted for review"}`);
      setText("");
      setReason("");
      reload();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <Notice>
        The original requirement is <strong>never overwritten</strong>. A change request records the original, proposed and approved
        text, the reason, who proposed it and who reviewed it. Approved changes are applied by the next regeneration, which
        creates a new analysis version. Reviewers can never approve their own proposals (four-eyes principle).
      </Notice>
      <Section title="Propose a requirement change">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Change type</Label>
            <Select value={type} onValueChange={(v) => setType(v as RequirementChange["change_type"])} disabled={!canPropose}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{CHANGE_TYPES.map(([v, t]) => <SelectItem key={v} value={v}>{t}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {type === "new" ? (
            <div className="space-y-2">
              <Label>New requirement kind</Label>
              <Select value={kind} onValueChange={setKind} disabled={!canPropose}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {["functional", "non_functional", "business_rule", "constraint"].map((k) => <SelectItem key={k} value={k}>{label(k)}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          ) : (
            <div className="space-y-2">
              <Label>Requirement</Label>
              <Select value={reqId} onValueChange={setReqId} disabled={!canPropose}>
                <SelectTrigger className="w-full"><SelectValue placeholder="Choose a requirement" /></SelectTrigger>
                <SelectContent className="max-h-72">
                  {requirements.map((r) => (
                    <SelectItem key={r.req_id} value={r.req_id}>{r.req_id} - {r.statement.slice(0, 70)}{r.statement.length > 70 ? "…" : ""}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {current && type !== "new" && (
            <div className="md:col-span-2">
              <p className="mb-1 text-xs font-medium text-muted-foreground">Current statement (read-only)</p>
              <p className="rounded-md bg-muted p-2 text-sm">{current.statement}</p>
            </div>
          )}
          {type !== "obsolete" && (
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="proposed">{type === "acceptance_criteria" ? "Acceptance criterion to add" : "Proposed text"}</Label>
              <Textarea id="proposed" rows={3} maxLength={3000} value={text} disabled={!canPropose} onChange={(e) => setText(e.target.value)} />
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="reason">Reason (required)</Label>
            <Textarea id="reason" rows={2} maxLength={2000} value={reason} disabled={!canPropose} onChange={(e) => setReason(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="source">Source of the change</Label>
            <Input id="source" value={source} maxLength={200} disabled={!canPropose} placeholder="e.g. Clarification Q-03, Interview SI-02"
              onChange={(e) => setSource(e.target.value)} />
            <label className="flex items-center gap-2 pt-1 text-sm">
              <Checkbox checked={asDraft} disabled={!canPropose} onCheckedChange={(v) => setAsDraft(!!v)} /> Save as draft (do not submit yet)
            </label>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button disabled={saving || !canPropose} onClick={submit}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <FilePlus2 className="size-4" />} {asDraft ? "Save draft" : "Submit for review"}
          </Button>
        </div>
        {!can("change:propose") && <p className="mt-2 text-xs text-muted-foreground">Your role cannot propose changes.</p>}
      </Section>

      <Section title={`Change requests (${data.changes.length})`}>
        {data.changes.length === 0 ? <EmptyState icon={Wrench} title="No change requests yet" /> : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Change</TableHead><TableHead>Requirement</TableHead><TableHead>Original → proposed</TableHead>
                  <TableHead>Reason / source</TableHead><TableHead>Proposed by</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.changes.map((c) => (
                  <TableRow key={c.id} className="align-top">
                    <TableCell className="whitespace-nowrap"><p className="font-mono text-xs">{c.code}</p><p className="text-xs text-muted-foreground">{CHANGE_LABEL[c.change_type]}</p></TableCell>
                    <TableCell className="font-mono text-xs">{c.requirement_id || "new"}</TableCell>
                    <TableCell className="min-w-[300px] max-w-[440px] whitespace-normal text-xs">
                      {c.original_text && <p className="text-muted-foreground line-through decoration-muted-foreground/40">{c.original_text}</p>}
                      <p className="mt-1">{c.change_type === "obsolete" ? "Mark as obsolete" : c.approved_text || c.proposed_text}</p>
                      {c.approved_text && c.approved_text !== c.proposed_text && <p className="mt-1 text-[11px] text-muted-foreground">Proposed: {c.proposed_text}</p>}
                    </TableCell>
                    <TableCell className="min-w-[180px] whitespace-normal text-xs">{c.reason}<p className="text-muted-foreground">Source: {c.source}</p></TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{c.proposed_by}<p className="text-muted-foreground">{formatDate(c.created_at)}</p></TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      <ApprovalStatus status={c.status} />
                      {c.reviewed_by && <p className="mt-1 text-muted-foreground">by {c.reviewed_by}</p>}
                      {c.review_comment && <p className="text-muted-foreground">&ldquo;{c.review_comment}&rdquo;</p>}
                      {c.applied_in_version && <Badge variant="outline" className="mt-1 font-normal">Applied in v{c.applied_in_version}</Badge>}
                    </TableCell>
                    <TableCell className="text-right">
                      {c.status === "pending_review" && <ChangeReviewActions change={c} reload={reload} readOnly={readOnly} />}
                      {(c.status === "draft" || c.status === "changes_requested") && c.proposed_by === user?.email && !readOnly && (
                        <ResubmitAction change={c} reload={reload} />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Section>
    </div>
  );
}

// ==========================================================================
// f. Regeneration
// ==========================================================================
function PlanGroup({ title, entries, tone }: { title: string; entries: RegenerationPlan["will_rerun"]; tone: string }) {
  return (
    <div className="rounded-lg border p-4">
      <p className={`mb-2 text-sm font-medium ${tone}`}>{title} ({entries.length})</p>
      {entries.length === 0 ? <p className="text-xs text-muted-foreground">None</p> : (
        <ul className="space-y-2">
          {entries.map((e) => (
            <li key={e.key} className="text-sm">
              <p className="font-medium">{e.agent}</p>
              <p className="text-xs text-muted-foreground">Output: {e.output}</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs">{e.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RegenerationProgress({ analysisId, onDone }: { analysisId: number; onDone: (status: AnalysisBrief) => void }) {
  const [status, setStatus] = useState<AnalysisBrief | null>(null);
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const s = await api<AnalysisBrief>(`/api/analyses/${analysisId}/status`);
        if (stopped) return;
        setStatus(s);
        if (DONE.includes(s.status) || s.status === "failed") {
          onDone(s);
          return;
        }
      } catch {
        /* keep polling */
      }
      if (!stopped) setTimeout(poll, 1000);
    };
    poll();
    return () => { stopped = true; };
  }, [analysisId, onDone]);
  return (
    <Card className="border-primary/30">
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-3 text-sm">
          <p className="flex items-center gap-2 font-medium"><Loader2 className="size-4 animate-spin text-primary" /> Selective regeneration running</p>
          <span className="tabular-nums">{status?.progress ?? 0}%</span>
        </div>
        <Progress value={status?.progress ?? 0} />
        <p className="text-xs text-muted-foreground">Current step: {status?.current_stage ?? "Queued"} · version v{status?.version ?? "…"} is being created</p>
      </CardContent>
    </Card>
  );
}

function RegenerationTab({ analysisId, data, readOnly, reload }: { analysisId: number; data: Overview; readOnly: boolean; reload: () => void }) {
  const { can } = useAuth();
  const router = useRouter();
  const [plan, setPlan] = useState<RegenerationPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [comment, setComment] = useState("");
  const [starting, setStarting] = useState(false);
  const [running, setRunning] = useState<number | null>(null);

  const loadPlan = useCallback(() => {
    setError(null);
    api<RegenerationPlan>(`/api/analyses/${analysisId}/regeneration/plan`).then(setPlan).catch((e) => setError(errorMessage(e)));
  }, [analysisId]);
  useEffect(loadPlan, [loadPlan]);

  const start = async () => {
    setStarting(true);
    try {
      const result = await api<{ run_id: number; new_analysis_id: number; new_version: number }>(
        `/api/analyses/${analysisId}/regeneration`, { body: { confirm: true, comment } });
      setConfirming(false);
      setRunning(result.new_analysis_id);
      toast.success(`Creating version v${result.new_version}…`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setStarting(false);
    }
  };

  const finished = useCallback((s: AnalysisBrief) => {
    if (s.status === "failed") {
      toast.error("Regeneration failed", { description: s.error });
      setRunning(null);
      reload();
      loadPlan();
      return;
    }
    toast.success(`Version v${s.version} is ready`);
    router.push(`/analyses/${s.id}`);
  }, [router, reload, loadPlan]);

  const disabled = readOnly || !plan?.has_inputs || !plan?.is_latest || !can("regeneration:run");
  const willRun = plan ? [...plan.will_rerun, ...plan.always_rerun] : [];

  return (
    <div className="space-y-6">
      {running ? <RegenerationProgress analysisId={running} onDone={finished} /> : null}
      <Section title="Regeneration plan" description="Dependency analysis of the approved changes, new answers and accepted interviews not yet applied."
        actions={<Button size="sm" variant="ghost" onClick={loadPlan}><RefreshCcw className="size-4" /> Refresh</Button>}>
        {error ? <ErrorState message={error} retry={loadPlan} /> : !plan ? <LoadingState text="Analysing dependencies…" /> : (
          <div className="space-y-4">
            {!plan.has_inputs && (
              <Notice>Nothing to regenerate yet. Approve a requirement change, answer a question or accept a stakeholder interview first.</Notice>
            )}
            <div className="grid gap-3 sm:grid-cols-3">
              <StatTile label="Approved changes" value={plan.inputs.changes.length} hint={plan.inputs.changes.map((c) => c.code).join(", ") || undefined} />
              <StatTile label="New answers" value={plan.inputs.questions.length} hint={plan.inputs.questions.map((q) => q.code).join(", ") || undefined} />
              <StatTile label="Accepted interviews" value={plan.inputs.interviews.length} hint={plan.inputs.interviews.map((i) => i.role).join(", ") || undefined} />
            </div>
            {plan.changed_requirements.length > 0 && (
              <div><p className="mb-1 text-xs font-medium text-muted-foreground">Changed requirements</p><ReqIds ids={plan.changed_requirements} max={20} /></div>
            )}
            {plan.notes.length > 0 && <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">{plan.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
            <div className="grid gap-3 lg:grid-cols-2">
              <PlanGroup title="Will re-run" entries={plan.will_rerun} tone="text-primary" />
              <PlanGroup title="Re-runs only if upstream results change" entries={plan.may_rerun} tone="text-amber-800" />
              <PlanGroup title="Always re-run" entries={plan.always_rerun} tone="text-foreground" />
              <PlanGroup title="Retained from this version" entries={plan.retained} tone="text-emerald-800" />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {!can("regeneration:run") && <span className="text-xs text-muted-foreground">Requires a Business Analyst or Project Manager.</span>}
              <Button disabled={disabled || !!running} onClick={() => setConfirming(true)}>
                <RefreshCcw className="size-4" /> Review and regenerate
              </Button>
            </div>
          </div>
        )}
      </Section>

      <Section title={`Regeneration history (${data.regenerations.length})`}>
        {data.regenerations.length === 0 ? <p className="text-sm text-muted-foreground">No regenerations yet.</p> : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow><TableHead>Versions</TableHead><TableHead>Changed requirements</TableHead><TableHead>Agents re-run</TableHead>
                  <TableHead>Retained</TableHead><TableHead>Outputs updated</TableHead><TableHead>Status</TableHead><TableHead>By / when</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {data.regenerations.map((r) => (
                  <TableRow key={r.id} className="align-top">
                    <TableCell className="whitespace-nowrap text-sm">
                      v{r.previous_version} → {r.new_analysis_id ? <Link className="font-medium underline" href={`/analyses/${r.new_analysis_id}`}>v{r.new_version}</Link> : `v${r.new_version}`}
                    </TableCell>
                    <TableCell className="min-w-[140px]"><ReqIds ids={r.changed_requirements} max={4} /></TableCell>
                    <TableCell className="min-w-[200px] whitespace-normal text-xs">{r.agents_rerun.join(", ") || "–"}</TableCell>
                    <TableCell className="min-w-[200px] whitespace-normal text-xs text-muted-foreground">{r.outputs_retained.join(", ") || "–"}</TableCell>
                    <TableCell className="min-w-[180px] whitespace-normal text-xs">{r.outputs_updated.join(", ") || "–"}</TableCell>
                    <TableCell><StatusPill status={r.status} />{r.error && <p className="mt-1 max-w-[220px] text-xs text-red-700">{r.error}</p>}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{r.created_by}<p className="text-muted-foreground">{formatDate(r.created_at)}</p></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Section>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Confirm selective regeneration</DialogTitle>
            <DialogDescription>
              A new version <strong>v{(plan?.source_version ?? data.analysis.version) + 1}</strong> will be created. This version (v{plan?.source_version})
              is preserved unchanged and stays available for comparison.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">Agents that will run</p>
              <ul className="list-disc pl-5">{willRun.map((e) => <li key={e.key}>{e.agent}</li>)}</ul>
            </div>
            {plan && plan.may_rerun.length > 0 && (
              <p className="text-xs text-muted-foreground">Also re-run only if their inputs change: {plan.may_rerun.map((e) => e.agent).join(", ")}.</p>
            )}
            {plan && <p className="text-xs text-muted-foreground">Retained: {plan.retained.map((e) => e.agent).join(", ") || "none"}.</p>}
            <div className="space-y-2">
              <Label htmlFor="regen-comment">Comment (optional)</Label>
              <Textarea id="regen-comment" rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>Cancel</Button>
            <Button disabled={starting} onClick={start}>{starting && <Loader2 className="size-4 animate-spin" />} Regenerate</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ==========================================================================
// g. Approval history
// ==========================================================================
function ApprovalHistoryTab() {
  const { analysis } = useLoadedAnalysis();
  const [rows, setRows] = useState<(Approval & { version: number; analysis_id: number })[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const versions = useMemo(() => analysis.versions?.length ? analysis.versions : [{ id: analysis.id, version: analysis.version }], [analysis]);

  const load = useCallback(() => {
    setError(null);
    Promise.all(versions.map((v) => api<Approval[]>(`/api/approvals?analysis_id=${v.id}`)
      .then((list) => list.map((a) => ({ ...a, version: v.version, analysis_id: v.id })))))
      .then((lists) => setRows(lists.flat().sort((a, b) => b.timestamp.localeCompare(a.timestamp))))
      .catch((e) => setError(errorMessage(e)));
  }, [versions]);
  useEffect(load, [load]);

  if (error) return <ErrorState message={error} retry={load} />;
  if (!rows) return <LoadingState />;
  return (
    <Section title={`Approval history (${rows.length})`} description="All human decisions on every version of this analysis.">
      {rows.length === 0 ? <EmptyState title="No decisions yet" /> : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow><TableHead>When</TableHead><TableHead>Version</TableHead><TableHead>Target</TableHead><TableHead>Action</TableHead>
                <TableHead>Reviewer</TableHead><TableHead>Comment</TableHead><TableHead>Previous → new</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((h) => (
                <TableRow key={`${h.analysis_id}-${h.id}`}>
                  <TableCell className="whitespace-nowrap text-xs">{formatDate(h.timestamp)}</TableCell>
                  <TableCell><Link className="text-xs underline" href={`/analyses/${h.analysis_id}`}>v{h.version}</Link></TableCell>
                  <TableCell className="text-xs">{label(h.target_type)} <span className="font-mono">{h.target_id}</span></TableCell>
                  <TableCell>
                    <StatusPill status={["approve", "accept"].includes(h.action) ? "approved" : h.action === "reject" ? "rejected"
                      : h.action === "flag" ? "flagged" : h.action === "request_changes" ? "changes_requested" : "modified"} text={label(h.action)} />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs"><p className="font-medium">{h.reviewer_email}</p><p className="text-muted-foreground">{ROLE_LABELS[h.reviewer_role] ?? h.reviewer_role}</p></TableCell>
                  <TableCell className="max-w-[220px] whitespace-normal text-xs">{h.comment || "–"}</TableCell>
                  <TableCell className="max-w-[340px] whitespace-normal text-xs text-muted-foreground">{compact(h.previous_value)} <span className="text-foreground">→</span> {compact(h.new_value)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  );
}

// ==========================================================================
// h. Version comparison
// ==========================================================================
function CompareTab() {
  const { analysis } = useLoadedAnalysis();
  const completed = useMemo(() => (analysis.versions ?? []).filter((v) => DONE.includes(v.status)), [analysis.versions]);
  const [base, setBase] = useState<string>("");
  const [target, setTarget] = useState<string>("");
  const [result, setResult] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (completed.length >= 2) {
      setTarget(String(completed[completed.length - 1].id));
      setBase(String(completed[completed.length - 2].id));
    }
  }, [completed]);

  useEffect(() => {
    if (!base || !target || base === target) { setResult(null); return; }
    setLoading(true);
    setError(null);
    api<Comparison>(`/api/analysis-versions/compare?base=${base}&target=${target}`).then(setResult)
      .catch((e) => setError(errorMessage(e))).finally(() => setLoading(false));
  }, [base, target]);

  if (completed.length < 2) {
    return <EmptyState icon={GitCompare} title="Only one version so far" description="Versions appear after a regeneration. Approve changes or answer questions, then regenerate in the Regeneration tab." />;
  }
  const versionLabel = (id: string) => `v${completed.find((v) => String(v.id) === id)?.version ?? "?"}`;
  const checks = result ? [...new Set([...Object.keys(result.validation.before), ...Object.keys(result.validation.after)])]
    .filter((c) => result.validation.before[c] !== result.validation.after[c]) : [];
  return (
    <div className="space-y-6">
      <Section title="Compare versions">
        <div className="flex flex-wrap items-end gap-3">
          {([["Base", base, setBase], ["Compare with", target, setTarget]] as [string, string, (v: string) => void][]).map(([title, value, setter]) => (
            <div key={title} className="space-y-1">
              <Label>{title}</Label>
              <Select value={value} onValueChange={setter}>
                <SelectTrigger className="w-[220px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {completed.map((v) => <SelectItem key={v.id} value={String(v.id)}>v{v.version} · #{v.id} · {v.recommended_sdlc || "–"}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          ))}
          <div className="flex gap-2 pb-0.5">
            {base && <Button size="sm" variant="outline" asChild><Link href={`/analyses/${base}`}>Open {versionLabel(base)}</Link></Button>}
            {target && <Button size="sm" variant="outline" asChild><Link href={`/analyses/${target}`}>Open {versionLabel(target)}</Link></Button>}
          </div>
        </div>
        {base === target && <p className="mt-3 text-sm text-muted-foreground">Choose two different versions.</p>}
      </Section>
      {loading ? <LoadingState text="Comparing versions…" /> : error ? <ErrorState message={error} /> : result && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile label="Requirements added" value={result.requirements.added.length} />
            <StatTile label="Requirements removed" value={result.requirements.removed.length} />
            <StatTile label="Requirements changed" value={result.requirements.changed.length} />
            <StatTile label="Recategorised" value={result.requirements.recategorised.length} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title="SDLC recommendation">
              <div className="grid grid-cols-2 gap-3 text-sm">
                {([["Before", result.sdlc.before], ["After", result.sdlc.after]] as [string, Comparison["sdlc"]["before"]][]).map(([title, v]) => (
                  <div key={title} className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">{title}</p>
                    <p className="font-medium">{v?.model ?? "–"}</p>
                    <p className="text-xs tabular-nums text-muted-foreground">{v ? `${v.score}/100` : ""}</p>
                  </div>
                ))}
              </div>
              {result.sdlc.factors_changed.length > 0 ? (
                <ul className="mt-3 space-y-1 text-sm">
                  {result.sdlc.factors_changed.map((f) => <li key={f.factor}>{f.factor}: {f.before}/5 → <strong>{f.after}/5</strong></li>)}
                </ul>
              ) : <p className="mt-3 text-xs text-muted-foreground">No decision factor changed.</p>}
            </Section>
            <Section title="SDLC workflow">
              <div className="grid grid-cols-2 gap-3 text-sm">
                {([["Before", result.workflow.before], ["After", result.workflow.after]] as [string, Comparison["workflow"]["before"]][]).map(([title, v]) => (
                  <div key={title} className="rounded-md border p-3">
                    <p className="text-xs text-muted-foreground">{title}</p>
                    <p className="font-medium">{v?.model ?? "–"}</p>
                    <p className="text-xs text-muted-foreground">{v ? `${v.phases} phases` : ""}</p>
                  </div>
                ))}
              </div>
              {result.workflow.customisations_added.length > 0 && (
                <div className="mt-3"><p className="text-xs font-medium text-emerald-800">Customisations added</p>
                  <ul className="list-disc pl-5 text-xs">{result.workflow.customisations_added.map((c) => <li key={c}>{c}</li>)}</ul></div>
              )}
              {result.workflow.customisations_removed.length > 0 && (
                <div className="mt-3"><p className="text-xs font-medium text-red-700">Customisations removed</p>
                  <ul className="list-disc pl-5 text-xs">{result.workflow.customisations_removed.map((c) => <li key={c}>{c}</li>)}</ul></div>
              )}
            </Section>
          </div>
          <Section title="Metric changes">
            {result.metrics.length === 0 ? <p className="text-sm text-muted-foreground">No metric changed.</p> : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Metric</TableHead><TableHead className="text-right">{versionLabel(base)}</TableHead><TableHead className="text-right">{versionLabel(target)}</TableHead><TableHead className="text-right">Change</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {result.metrics.map((m) => {
                      const delta = typeof m.before === "number" && typeof m.after === "number" ? Math.round((m.after - m.before) * 1000) / 1000 : null;
                      return (
                        <TableRow key={m.metric}>
                          <TableCell>{label(m.metric)}</TableCell>
                          <TableCell className="text-right tabular-nums">{m.before ?? "–"}</TableCell>
                          <TableCell className="text-right tabular-nums">{m.after ?? "–"}</TableCell>
                          <TableCell className="text-right tabular-nums">{delta === null ? "–" : delta > 0 ? `+${delta}` : delta}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>
          <Section title="Requirement differences">
            <div className="space-y-4 text-sm">
              {result.requirements.changed.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Changed</p>
                  <ul className="space-y-2">
                    {result.requirements.changed.map((r) => (
                      <li key={r.req_id} className="rounded-md border p-2">
                        <div className="flex items-center gap-2"><span className="font-mono text-xs">{r.req_id}</span><StatusPill status={r.status} /></div>
                        {r.before !== r.after ? (
                          <>
                            <p className="mt-1 text-xs text-muted-foreground line-through decoration-muted-foreground/40">{r.before}</p>
                            <p className="text-xs">{r.after}</p>
                          </>
                        ) : <p className="mt-1 text-xs">{r.after}</p>}
                        {r.supplementary_added?.length ? (
                          <ul className="mt-1 space-y-0.5">
                            {r.supplementary_added.map((s) => <li key={s} className="text-xs text-violet-800">+ {s}</li>)}
                          </ul>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {result.requirements.added.length > 0 && (
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Added</p>
                  <ul className="space-y-1">{result.requirements.added.map((r) => <li key={r.req_id} className="text-xs"><span className="mr-2 font-mono">{r.req_id}</span>{r.statement}</li>)}</ul></div>
              )}
              {result.requirements.removed.length > 0 && (
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Removed</p><ReqIds ids={result.requirements.removed} max={20} /></div>
              )}
              {result.requirements.recategorised.length > 0 && (
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Recategorised</p>
                  <ul className="space-y-1">{result.requirements.recategorised.map((r) => (
                    <li key={r.req_id} className="text-xs"><span className="mr-2 font-mono">{r.req_id}</span>{r.before.map(label).join(", ")} → {r.after.map(label).join(", ")}</li>
                  ))}</ul></div>
              )}
              {!result.requirements.changed.length && !result.requirements.added.length && !result.requirements.removed.length
                && !result.requirements.recategorised.length && <p className="text-muted-foreground">The requirement sets are identical.</p>}
            </div>
          </Section>
          <Section title="Validation changes">
            {checks.length === 0 ? <p className="text-sm text-muted-foreground">No validation check changed status.</p> : (
              <ul className="space-y-1 text-sm">
                {checks.map((c) => (
                  <li key={c} className="flex flex-wrap items-center gap-2">
                    <span>{c}</span>
                    {result.validation.before[c] && <StatusPill status={result.validation.before[c]} text={result.validation.before[c].toUpperCase()} />}
                    <ArrowRight className="size-3 text-muted-foreground" />
                    {result.validation.after[c] && <StatusPill status={result.validation.after[c]} text={result.validation.after[c].toUpperCase()} />}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

// ==========================================================================
// Page
// ==========================================================================
export default function RefinementCentrePage() {
  const { analysis, reload: reloadAnalysis } = useLoadedAnalysis();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState("overview");
  const [changeDraft, setChangeDraft] = useState<ChangeDraft | null>(null);
  const opened = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api<Overview>(`/api/analyses/${analysis.id}/refinement`));
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [analysis.id]);

  useEffect(() => {
    // Opening the Refinement Centre is the explicit action that starts Stage 2 (logged once per visit).
    if (opened.current === analysis.id) return;
    opened.current = analysis.id;
    api(`/api/analyses/${analysis.id}/refinement/open`, { method: "POST" })
      .then(() => reloadAnalysis())
      .catch(() => { /* Stage 1 not completed - the overview call shows the reason */ })
      .finally(load);
  }, [analysis.id, load, reloadAnalysis]);

  if (error) return <ErrorState message={error} retry={load} />;
  if (!data) return <LoadingState text="Opening the Refinement Centre…" />;
  const readOnly = !data.analysis.is_latest;
  const requirementIds = analysis.requirements.filter((r) => r.status !== "obsolete").map((r) => r.req_id);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Refinement Centre</h2>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Further refine your requirements, resolve outstanding issues, conduct stakeholder interviews and regenerate affected
            analysis outputs. Version v{data.analysis.version}.
          </p>
        </div>
        <StatusPill status={data.analysis.stage2_status} text={`Stage 2: ${label(data.analysis.stage2_status)}`} />
      </div>
      {readOnly && (
        <Notice tone="warning">
          You are viewing version v{data.analysis.version}. A newer version (v{data.analysis.latest_version}) exists, so refinement actions are
          read-only here.{" "}
          <Link className="font-medium underline" href={`/analyses/${data.analysis.latest_id}/refinement`}>Open the latest version&apos;s Refinement Centre</Link>.
        </Notice>
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <div className="overflow-x-auto pb-1">
          <TabsList className="w-max">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="issues">Outstanding Issues</TabsTrigger>
            <TabsTrigger value="clarification">Clarification</TabsTrigger>
            <TabsTrigger value="stakeholders">Stakeholder Interaction</TabsTrigger>
            <TabsTrigger value="changes">Requirement Changes</TabsTrigger>
            <TabsTrigger value="regeneration">Regeneration</TabsTrigger>
            <TabsTrigger value="approvals">Approval History</TabsTrigger>
            <TabsTrigger value="compare">Version Comparison</TabsTrigger>
          </TabsList>
        </div>
        <TabsContent value="overview" className="mt-4"><OverviewTab data={data} /></TabsContent>
        <TabsContent value="issues" className="mt-4">
          <IssuesTab data={data} readOnly={readOnly} onAsk={() => setTab("clarification")}
            onPropose={(draft) => { setChangeDraft({ ...draft }); setTab("changes"); }} />
        </TabsContent>
        <TabsContent value="clarification" className="mt-4"><ClarificationTab analysisId={analysis.id} readOnly={readOnly} onChanged={load} /></TabsContent>
        <TabsContent value="stakeholders" className="mt-4">
          <StakeholdersTab analysisId={analysis.id} data={data} readOnly={readOnly} requirementIds={requirementIds} reload={load} />
        </TabsContent>
        <TabsContent value="changes" className="mt-4">
          <ChangesTab analysisId={analysis.id} data={data} draft={changeDraft} readOnly={readOnly} reload={load} />
        </TabsContent>
        <TabsContent value="regeneration" className="mt-4"><RegenerationTab analysisId={analysis.id} data={data} readOnly={readOnly} reload={load} /></TabsContent>
        <TabsContent value="approvals" className="mt-4"><ApprovalHistoryTab /></TabsContent>
        <TabsContent value="compare" className="mt-4"><CompareTab /></TabsContent>
      </Tabs>
    </div>
  );
}
