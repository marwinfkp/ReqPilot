"use client";

import { ArrowRight, ChevronDown, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth";
import { useLoadedAnalysis } from "@/components/analysis-context";
import { BulletList, EmptyState, GeneratedBy, Notice, ReqIds, Section, StatusPill } from "@/components/common";
import { Evidence } from "@/components/evidence";
import { ReviewDialog } from "@/components/review-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { label } from "@/lib/format";
import type { Phase } from "@/lib/types";
import { cn } from "@/lib/utils";

const STAGE_STYLE: Record<string, string> = {
  plan: "bg-blue-50 text-blue-800 border-blue-200", design: "bg-violet-50 text-violet-800 border-violet-200",
  build: "bg-slate-100 text-slate-800 border-slate-200", verify: "bg-teal-50 text-teal-800 border-teal-200",
  accept: "bg-amber-50 text-amber-900 border-amber-200", release: "bg-orange-50 text-orange-800 border-orange-200",
  operate: "bg-emerald-50 text-emerald-800 border-emerald-200",
};

function Field({ title, items, mono = false }: { title: string; items: string[]; mono?: boolean }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      {mono ? <ReqIds ids={items} max={12} /> : <BulletList items={items} />}
    </div>
  );
}

function PhaseCard({ phase, open, toggle, last }: { phase: Phase; open: boolean; toggle: () => void; last: boolean }) {
  return (
    <li id={`phase-${phase.order}`} className="relative pl-12">
      {!last && <span className="absolute left-[19px] top-10 h-[calc(100%-1rem)] w-px bg-border" />}
      <span className="absolute left-0 top-1 flex size-10 items-center justify-center rounded-full border-2 border-primary bg-background text-sm font-semibold text-primary">{phase.order}</span>
      <div className="mb-6 rounded-xl border bg-card">
        <button onClick={toggle} className="flex w-full items-start justify-between gap-4 p-4 text-left">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-semibold">{phase.name}</p>
              <span className={cn("rounded border px-1.5 py-0.5 text-[11px] font-medium", STAGE_STYLE[phase.stage])}>{label(phase.stage)}</span>
              {phase.approval_gates.length > 0 && <Badge variant="outline" className="gap-1 font-normal"><UserCheck className="size-3" />{phase.approval_gates.length} approval gate(s)</Badge>}
              {phase.customised.length > 0 && <Badge variant="outline" className="gap-1 font-normal"><Sparkles className="size-3" />{phase.customised.length} project-specific</Badge>}
              <GeneratedBy value={phase.generated_by} />
            </div>
            <p className="text-sm text-muted-foreground">{phase.description}</p>
            {phase.verifies && <p className="text-xs text-primary">Verifies ↔ {phase.verifies}</p>}
          </div>
          <ChevronDown className={cn("mt-1 size-4 shrink-0 transition-transform", open && "rotate-180")} />
        </button>
        {open && (
          <div className="space-y-5 border-t p-4">
            {phase.approval_gates.length > 0 && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 p-3">
                <p className="mb-2 flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-primary" /> Human approval gates</p>
                <ul className="space-y-1 text-sm">{phase.approval_gates.map((g) => <li key={g.name}><span className="font-medium">{g.name}</span> - approved by {g.approvers.join(", ")}</li>)}</ul>
              </div>
            )}
            <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
              <Field title="Activities" items={phase.activities} />
              <Field title="Responsible roles" items={phase.roles} />
              <Field title="Deliverables" items={phase.deliverables} />
              <Field title="Security checkpoints" items={phase.security} />
              <Field title="Testing activities" items={phase.testing} />
              <Field title="Compliance checkpoints" items={phase.compliance} />
              <Field title="Entry criteria" items={phase.entry_criteria} />
              <Field title="Exit criteria" items={phase.exit_criteria} />
              <Field title="Traceability checkpoints" items={phase.traceability} />
            </div>
            {phase.key_requirements.length > 0 && <Field title="Key requirements in this phase" items={phase.key_requirements} mono />}
            {phase.customised.length > 0 && <p className="text-xs text-muted-foreground">Customised because: {[...new Set(phase.customised)].join(" · ")}</p>}
            {(phase.citations?.length ?? 0) > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Engineering practice this phase follows</p>
                <Evidence citations={phase.citations} />
              </div>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export default function WorkflowPage() {
  const { analysis: a, reload } = useLoadedAnalysis();
  const { can } = useAuth();
  const wf = a.workflow;
  const [open, setOpen] = useState<Set<number>>(new Set([1]));
  if (!wf) return <EmptyState title="No workflow generated" description="The workflow agent did not complete; see the agent log." />;

  const sdlcPending = a.sdlc?.status === "pending_approval";
  const toggle = (order: number) => setOpen((s) => { const n = new Set(s); if (n.has(order)) n.delete(order); else n.add(order); return n; });
  const gates = wf.phases.reduce((n, p) => n + p.approval_gates.length, 0);
  const decide = async (action: string, comment: string) => {
    await api(`/api/analyses/${a.id}/workflow/review`, { body: { action, comment } });
    await reload();
  };

  return (
    <div className="space-y-6">
      <Section
        title={<span className="flex flex-wrap items-center gap-2">{wf.sdlc_model} workflow <StatusPill status={wf.status} /></span>}
        description={`${wf.phases.length} phases · ${gates} human approval gates · ${wf.customisations.length} project-specific customisations for ${a.project.name}`}
        actions={
          <div className="flex flex-wrap gap-2">
            <ReviewDialog title="Approve workflow" confirmLabel="Approve" disabled={!can("workflow:approve") || sdlcPending || wf.status === "approved"}
              trigger={<Button size="sm" disabled={!can("workflow:approve") || sdlcPending || wf.status === "approved"}>Approve workflow</Button>}
              onSubmit={(c) => decide("approve", c)} />
            <ReviewDialog title="Request changes" confirmLabel="Request changes" commentRequired disabled={!can("workflow:approve")}
              trigger={<Button size="sm" variant="outline" disabled={!can("workflow:approve")}>Request changes</Button>}
              onSubmit={(c) => decide("request_changes", c)} />
          </div>
        }>
        {sdlcPending && <div className="mb-4"><Notice tone="warning">The SDLC recommendation must be approved or overridden by a Project Manager before this workflow can be approved.</Notice></div>}
        <div className="flex gap-1 overflow-x-auto pb-1">
          {wf.phases.map((p, i) => (
            <div key={p.order} className="flex shrink-0 items-center">
              <a href={`#phase-${p.order}`} onClick={() => setOpen((s) => new Set(s).add(p.order))}
                className={cn("rounded-lg border px-3 py-2 text-xs font-medium transition-colors hover:border-primary", STAGE_STYLE[p.stage])}>
                <span className="opacity-60">{p.order}.</span> {p.name}
              </a>
              {i < wf.phases.length - 1 && <ArrowRight className="mx-1 size-3.5 text-muted-foreground" />}
            </div>
          ))}
        </div>
      </Section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div>
          <div className="mb-3 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(new Set(wf.phases.map((p) => p.order)))}>Expand all</Button>
            <Button variant="ghost" size="sm" onClick={() => setOpen(new Set())}>Collapse all</Button>
          </div>
          <ol>{wf.phases.map((p, i) => <PhaseCard key={p.order} phase={p} open={open.has(p.order)} toggle={() => toggle(p.order)} last={i === wf.phases.length - 1} />)}</ol>
        </div>
        <Section title="Why it is project-specific" description="Customisations applied from this project's analysis.">
          <ul className="space-y-3 text-sm">
            {wf.customisations.map((c, i) => (
              <li key={i} className="border-l-2 border-primary/40 pl-3">
                <p className="text-xs font-medium text-primary">{c.reason}</p>
                <p>{c.item}</p>
                <p className="text-xs text-muted-foreground">→ {c.phase} · {label(c.field)}</p>
              </li>
            ))}
          </ul>
        </Section>
      </div>
    </div>
  );
}
