"use client";

// Clarification Centre - used in Stage 1 (before the full analysis) and in the
// Stage 2 Refinement Centre. Answers are saved one question at a time; nothing
// forces the user to answer everything.
import { AlertTriangle, ArrowRight, ChevronDown, ChevronLeft, ChevronRight, Loader2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { EmptyState, LoadingState, Notice, SeverityBadge, StatTile, StatusPill } from "@/components/common";
import { Evidence } from "@/components/evidence";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { formatDate, label } from "@/lib/format";
import type { ClarificationQuestion } from "@/lib/types";
import { cn } from "@/lib/utils";

type Props = {
  analysisId: number;
  /** Which questions to show (e.g. Stage 1 only, or Stage 2 + still-unresolved Stage 1). */
  filter?: (q: ClarificationQuestion) => boolean;
  /** Stage 1 only: show the "Continue to Full Analysis" action. */
  onContinue?: () => Promise<void>;
  /** Bump to reload questions after another component generated new ones. */
  refreshKey?: number;
  header?: React.ReactNode;
  stage: 1 | 2;
};

function AnswerField({ q, value, onChange, disabled }: { q: ClarificationQuestion; value: string; onChange: (v: string) => void; disabled: boolean }) {
  const [other, setOther] = useState("");
  const parts = value.split(";").map((p) => p.trim()).filter(Boolean);
  const otherPart = parts.find((p) => p.toLowerCase().startsWith("other"));
  useEffect(() => { if (otherPart) setOther(otherPart.replace(/^other:?\s*/i, "")); }, [otherPart]);

  if (q.answer_format === "single_choice" || q.answer_format === "multiple_choice") {
    const multiple = q.answer_format === "multiple_choice";
    const chosen = new Set(parts.filter((p) => !p.toLowerCase().startsWith("other")));
    const update = (next: Set<string>, otherText: string | null) => {
      const values = [...next];
      if (otherText !== null && otherText.trim()) values.push(`Other: ${otherText.trim()}`);
      onChange(values.join("; "));
    };
    return (
      <div className="space-y-2">
        {q.options.map((option) => (
          <label key={option} className={cn("flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted/60",
            chosen.has(option) && "border-primary bg-primary/5")}>
            <input type={multiple ? "checkbox" : "radio"} name={`q-${q.id}`} disabled={disabled} className="mt-0.5 accent-[var(--series-1)]"
              checked={chosen.has(option)}
              onChange={() => {
                const next = multiple ? new Set(chosen) : new Set<string>();
                if (multiple && chosen.has(option)) next.delete(option); else next.add(option);
                update(next, multiple ? other : null);
              }} />
            {option}
          </label>
        ))}
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Other:</span>
          <Input value={other} disabled={disabled} placeholder="Describe another answer"
            onChange={(e) => { setOther(e.target.value); update(multiple ? chosen : new Set(), e.target.value); }} />
        </div>
      </div>
    );
  }
  if (q.answer_format === "yes_no") {
    const yes = /^yes\b/i.test(value), no = /^no\b/i.test(value);
    const note = value.replace(/^(yes|no)\b\s*[-:,]?\s*/i, "");
    return (
      <div className="space-y-2">
        <div className="flex gap-2">
          {["Yes", "No"].map((choice) => (
            <Button key={choice} type="button" size="sm" disabled={disabled} variant={(choice === "Yes" ? yes : no) ? "default" : "outline"}
              onClick={() => onChange(note ? `${choice} - ${note}` : choice)}>{choice}</Button>
          ))}
        </div>
        <Input value={note} disabled={disabled || !(yes || no)} placeholder="Optional explanation"
          onChange={(e) => onChange(`${yes ? "Yes" : "No"}${e.target.value ? ` - ${e.target.value}` : ""}`)} />
      </div>
    );
  }
  if (q.answer_format === "long_text") {
    return <Textarea rows={4} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder="Your answer" />;
  }
  return (
    <div className="space-y-1">
      <Input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}
        placeholder={q.answer_format === "numeric" ? "A number, with units (e.g. 2 seconds, 500 TPS)" : "Your answer"} />
      {q.answer_format === "numeric" && <p className="text-xs text-muted-foreground">Numeric answer expected - units and a short note are allowed.</p>}
    </div>
  );
}

function QuestionCard({ q, open, onToggle, editable, provisional, onSaved }: {
  q: ClarificationQuestion; open: boolean; onToggle: () => void; editable: boolean; provisional: boolean;
  onSaved: (q: ClarificationQuestion) => void;
}) {
  const [draft, setDraft] = useState(q.answer);
  const [editing, setEditing] = useState(q.status !== "answered");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { setDraft(q.answer); setEditing(q.status !== "answered"); }, [q.answer, q.status]);
  const locked = !editable || q.applied_in_version !== null;

  const save = async (status: ClarificationQuestion["status"], text = draft) => {
    setBusy(status);
    try {
      const updated = await api<ClarificationQuestion>(`/api/clarifications/${q.id}`, { method: "PUT", body: { status, answer: text } });
      onSaved(updated);
      toast.success(`${q.code} ${status === "answered" ? "answered" : label(status).toLowerCase()}`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card id={`question-${q.id}`} className={cn("gap-0 py-0 transition-shadow", open && "ring-1 ring-primary/30")}>
      <button type="button" onClick={onToggle} className="flex w-full items-start gap-3 p-4 text-left">
        <span className="mt-0.5 font-mono text-xs text-muted-foreground">{q.code}</span>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-sm font-medium leading-snug">{q.question}</p>
          <div className="flex flex-wrap items-center gap-1.5">
            <SeverityBadge severity={q.priority} />
            <Badge variant="secondary" className="font-normal">{label(q.category)}</Badge>
            {q.issue_type === "contradiction" && <Badge variant="outline" className="font-normal">Contradiction</Badge>}
            {q.issue_type === "missing_information" && <Badge variant="outline" className="font-normal">Missing information</Badge>}
            <StatusPill status={q.status} text={q.status === "needs_stakeholder" ? "Needs stakeholder input" : undefined} />
            {q.resolution && <StatusPill status={q.resolution} />}
            {q.applied_in_version && <Badge variant="outline" className="font-normal">Applied in v{q.applied_in_version}</Badge>}
          </div>
        </div>
        <ChevronDown className={cn("mt-1 size-4 shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <CardContent className="space-y-4 border-t px-4 pb-4 pt-4 text-sm">
          <div className="grid gap-3 md:grid-cols-2">
            <div><p className="text-xs font-medium text-muted-foreground">Why clarification is needed</p><p>{q.reason}</p></div>
            <div><p className="text-xs font-medium text-muted-foreground">Impact if left unresolved</p><p>{q.impact}</p></div>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            {q.stakeholder_role && <span>Suggested stakeholder: <strong className="text-foreground">{q.stakeholder_role}</strong></span>}
            <span>Answer format: {label(q.answer_format)}</span>
            <span>Generated by: {q.generated_by.startsWith("openai:") ? `OpenAI (${q.generated_by.slice(7)})` : "rule-based checks"}</span>
          </div>
          {(q.related_requirements.length > 0 || q.requirement_ids.length > 0) && (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                Related requirements {provisional && <span className="font-normal">(provisional IDs - final IDs are assigned by the full analysis)</span>}
              </p>
              <ul className="space-y-1">
                {q.related_requirements.map((r) => (
                  <li key={r.ref} className="rounded-md bg-muted px-2 py-1 text-xs"><span className="mr-2 font-mono">{r.ref}</span>{r.text}</li>
                ))}
                {q.related_requirements.length === 0 && q.requirement_ids.map((id) => <li key={id} className="font-mono text-xs">{id}</li>)}
              </ul>
            </div>
          )}
          {(q.citations?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground">Knowledge-base passages this question relies on</p>
              <Evidence citations={q.citations} className="mt-1" />
            </div>
          )}
          {q.status === "answered" && !editing ? (
            <div className="rounded-md border border-emerald-200 bg-emerald-50/60 p-3">
              <p className="text-xs font-medium text-emerald-900">Answer</p>
              <p className="whitespace-pre-line">{q.answer}</p>
              <p className="mt-1 text-xs text-muted-foreground">by {q.responded_by} · {formatDate(q.responded_at)}{q.answered_in_stage === 2 && " · given in Stage 2"}</p>
              {!locked && <Button variant="outline" size="sm" className="mt-2" onClick={() => setEditing(true)}>Edit answer</Button>}
            </div>
          ) : (
            <div className="space-y-2">
              <Label>Your answer</Label>
              <AnswerField q={q} value={draft} onChange={setDraft} disabled={locked || !!busy} />
              {q.status !== "open" && q.status !== "answered" && q.answer && <p className="text-xs text-muted-foreground">Note: {q.answer}</p>}
            </div>
          )}
          {!locked && (
            <div className="flex flex-wrap gap-2">
              {(editing || q.status !== "answered") && (
                <Button size="sm" disabled={!!busy || !draft.trim()} onClick={() => save("answered")}>
                  {busy === "answered" && <Loader2 className="size-4 animate-spin" />} Save answer
                </Button>
              )}
              {q.status !== "deferred" && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => save("deferred", q.status === "answered" ? "" : draft)}>Defer</Button>}
              {q.status !== "needs_stakeholder" && <Button size="sm" variant="outline" disabled={!!busy} onClick={() => save("needs_stakeholder", q.status === "answered" ? "" : draft)}>Needs stakeholder input</Button>}
              {q.status !== "open" && <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => save("open", "")}><Undo2 className="size-4" /> Reopen</Button>}
            </div>
          )}
          {locked && !editable && <p className="text-xs text-muted-foreground">Read-only: answers cannot be changed while the analysis is running or after they were applied.</p>}
        </CardContent>
      )}
    </Card>
  );
}

export function ClarificationCentre({ analysisId, filter, onContinue, refreshKey = 0, header, stage }: Props) {
  const { can } = useAuth();
  const [all, setAll] = useState<ClarificationQuestion[] | null>(null);
  const [editable, setEditable] = useState(false);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [active, setActive] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const data = await api<{ questions: ClarificationQuestion[]; editable_stage1: boolean; preliminary: { note?: string }; status: string }>(
      `/api/analyses/${analysisId}/clarifications`);
    setAll(data.questions);
    setEditable(stage === 1 ? data.editable_stage1 : data.status === "completed" || data.status === "completed_with_warnings");
    setNote(data.preliminary?.note ?? "");
  }, [analysisId, stage]);

  useEffect(() => { load().catch((e) => toast.error(errorMessage(e))); }, [load, refreshKey]);

  const shown = useMemo(() => (all ?? []).filter(filter ?? (() => true)), [all, filter]);
  useEffect(() => { if (shown.length && open.size === 0) setOpen(new Set([shown[0].id])); }, [shown, open.size]);

  if (!all) return <LoadingState text="Loading clarification questions…" />;
  const count = (fn: (q: ClarificationQuestion) => boolean) => shown.filter(fn).length;
  const criticalOpen = shown.filter((q) => q.priority === "Critical" && q.status !== "answered");
  const goTo = (index: number) => {
    const target = shown[index];
    if (!target) return;
    setActive(index);
    setOpen(new Set([target.id]));
    setTimeout(() => document.getElementById(`question-${target.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };
  const saved = (updated: ClarificationQuestion) => setAll((current) => (current ?? []).map((q) => (q.id === updated.id ? updated : q)));
  const canAnswer = can("clarification:answer") && editable;

  const doContinue = async () => {
    if (!onContinue) return;
    setContinuing(true);
    try {
      await onContinue();
    } catch (e) {
      toast.error(errorMessage(e));
      setContinuing(false);
    }
  };

  return (
    <div className="space-y-5">
      {header}
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Questions" value={shown.length} />
        <StatTile label="Critical" value={count((q) => q.priority === "Critical")} tone={count((q) => q.priority === "Critical") ? "danger" : "default"} />
        <StatTile label="High priority" value={count((q) => q.priority === "High")} tone="warning" />
        <StatTile label="Answered" value={count((q) => q.status === "answered")} tone="good" />
        <StatTile label="Deferred" value={count((q) => q.status === "deferred")} />
        <StatTile label="Need stakeholder" value={count((q) => q.status === "needs_stakeholder")} />
      </div>
      {note && <Notice>{note}</Notice>}
      {!can("clarification:answer") && <Notice tone="warning">Your role can read these questions but cannot answer them.</Notice>}
      {criticalOpen.length > 0 && (
        <Notice tone="warning">
          {criticalOpen.length} critical question{criticalOpen.length > 1 ? "s are" : " is"} still unanswered ({criticalOpen.map((q) => q.code).join(", ")}).
          You can still continue - unanswered questions stay unresolved and are reported in the results; nothing will be assumed.
        </Notice>
      )}
      {shown.length === 0 ? (
        <EmptyState title="No clarification questions" description="The requirements were clear enough that no clarification was needed." />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" disabled={active <= 0} onClick={() => goTo(active - 1)}><ChevronLeft className="size-4" /> Previous</Button>
              <span className="text-sm tabular-nums text-muted-foreground">Question {Math.min(active + 1, shown.length)} of {shown.length}</span>
              <Button variant="outline" size="sm" disabled={active >= shown.length - 1} onClick={() => goTo(active + 1)}>Next <ChevronRight className="size-4" /></Button>
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={() => setOpen(new Set(shown.map((q) => q.id)))}>Review all</Button>
              <Button variant="ghost" size="sm" onClick={() => setOpen(new Set())}>Collapse all</Button>
            </div>
          </div>
          <div className="space-y-3">
            {shown.map((q, index) => (
              <QuestionCard key={q.id} q={q} open={open.has(q.id)} editable={canAnswer} provisional={stage === 1 && q.stage === 1 && !q.requirement_ids.length}
                onSaved={saved}
                onToggle={() => { setActive(index); setOpen((s) => { const n = new Set(s); if (n.has(q.id)) n.delete(q.id); else n.add(q.id); return n; }); }} />
            ))}
          </div>
        </>
      )}
      {onContinue && (
        <Card className="sticky bottom-4 z-10 border-primary/30 shadow-lg">
          <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-muted-foreground">
              {count((q) => q.status === "answered")} of {shown.length} answered. Answers are saved as you go - you can leave and resume later.
            </p>
            <Button size="lg" disabled={continuing || !can("analysis:run")} onClick={() => (criticalOpen.length ? setConfirming(true) : doContinue())}>
              {continuing ? <Loader2 className="size-4 animate-spin" /> : <ArrowRight className="size-4" />} Continue to Full Analysis
            </Button>
          </CardContent>
        </Card>
      )}
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2"><AlertTriangle className="size-5 text-amber-600" /> Critical questions unanswered</DialogTitle>
            <DialogDescription>
              {criticalOpen.length} critical question{criticalOpen.length > 1 ? "s remain" : " remains"} unanswered. The full analysis will still run with all 12 agents;
              these questions will be recorded as unresolved, raised as a risk, and can be answered later in the Refinement Centre.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(false)}>Keep answering</Button>
            <Button onClick={() => { setConfirming(false); doContinue(); }}>Continue anyway</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

