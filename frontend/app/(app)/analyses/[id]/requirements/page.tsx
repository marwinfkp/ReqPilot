"use client";

import { Check, Pencil, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth";
import { useLoadedAnalysis } from "@/components/analysis-context";
import { Meter } from "@/components/charts";
import { AuthenticityBadge, BulletList, Chips, GeneratedBy, Notice, ReqIds, Section, SeverityBadge, StatusPill } from "@/components/common";
import { Evidence } from "@/components/evidence";
import { ReviewDialog } from "@/components/review-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { formatDate, label } from "@/lib/format";
import type { Requirement } from "@/lib/types";

const ALL = "all";

function FilterSelect({ value, onChange, options, placeholder }: { value: string; onChange: (v: string) => void; options: string[]; placeholder: string }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-full sm:w-[170px]"><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{placeholder}</SelectItem>
        {options.map((o) => <SelectItem key={o} value={o}>{label(o)}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

export default function RequirementsPage() {
  const { analysis: a, reload } = useLoadedAnalysis();
  const { can } = useAuth();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState(ALL);
  const [category, setCategory] = useState(ALL);
  const [priority, setPriority] = useState(ALL);
  const [risk, setRisk] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [selected, setSelected] = useState<Requirement | null>(null);
  const [edited, setEdited] = useState("");

  const categories = useMemo(() => [...new Set(a.requirements.flatMap((r) => r.categories))].sort(), [a.requirements]);
  const rows = a.requirements.filter((r) =>
    (!query || `${r.req_id} ${r.statement} ${r.source}`.toLowerCase().includes(query.toLowerCase()))
    && (kind === ALL || r.kind === kind) && (category === ALL || r.categories.includes(category))
    && (priority === ALL || r.priority === priority) && (risk === ALL || r.risk_level === risk)
    && (status === ALL || r.status === status));
  const pending = a.requirements.filter((r) => r.status === "pending_review").length;

  const canReview = (r: Requirement) => can("requirement:review")
    || (can("requirement:review_security") && r.categories.some((c) => c === "security" || c === "privacy"));

  const review = async (r: Requirement, action: string, comment: string, statement?: string) => {
    const updated = await api<Requirement>(`/api/requirements/${r.id}/review`, { body: { action, comment, statement } });
    setSelected(updated);
    await reload();
  };

  const findings = selected ? a.findings.filter((f) => f.requirement_ids.includes(selected.req_id)) : [];
  const mappings = selected ? a.mappings.filter((m) => m.requirement_id === selected.req_id) : [];
  const history = selected ? a.approvals.filter((h) => h.target_type === "requirement" && h.target_id === selected.req_id) : [];

  return (
    <div className="space-y-6">
      <Section
        title={`Requirements Explorer (${a.requirements.length})`}
        description="Structured requirements extracted from the submission. Click a requirement to inspect and review it."
        actions={
          <div className="flex flex-wrap gap-2">
            <ReviewDialog title="Accept all pending requirements" confirmLabel={`Accept ${pending}`}
              description="You accept every requirement that is still pending review. Rejected or modified requirements are not changed."
              disabled={!pending || !can("requirement:review")}
              trigger={<Button variant="outline" size="sm" disabled={!pending || !can("requirement:review")}>Accept {pending} pending</Button>}
              onSubmit={async (comment) => { await api(`/api/analyses/${a.id}/requirements/accept-pending`, { body: { comment } }); await reload(); }} />
            <ReviewDialog title="Approve requirement baseline" confirmLabel="Approve baseline"
              description="Freezes the reviewed requirement set as the approved baseline. Later changes reopen it for re-approval."
              disabled={a.baseline_status === "approved" || !can("baseline:approve")}
              trigger={<Button size="sm" disabled={a.baseline_status === "approved" || !can("baseline:approve")}>
                {a.baseline_status === "approved" ? "Baseline approved" : "Approve baseline"}</Button>}
              onSubmit={async (comment) => { await api(`/api/analyses/${a.id}/baseline`, { body: { comment } }); await reload(); }} />
          </div>
        }
      >
        <div className="mb-4 flex flex-col gap-2 lg:flex-row">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
            <Input className="pl-8" placeholder="Search ID, text or source…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <FilterSelect value={kind} onChange={setKind} placeholder="All kinds" options={["functional", "non_functional", "business_rule", "constraint"]} />
          <FilterSelect value={category} onChange={setCategory} placeholder="All categories" options={categories} />
          <FilterSelect value={priority} onChange={setPriority} placeholder="All priorities" options={["High", "Medium", "Low"]} />
          <FilterSelect value={risk} onChange={setRisk} placeholder="All risk levels" options={["High", "Medium", "Low"]} />
          <FilterSelect value={status} onChange={setStatus} placeholder="All statuses" options={["pending_review", "accepted", "modified", "rejected", "obsolete"]} />
        </div>
        <p className="mb-2 text-xs text-muted-foreground">Showing {rows.length} of {a.requirements.length}</p>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead><TableHead>Requirement</TableHead><TableHead>Categories</TableHead><TableHead>Priority</TableHead>
                <TableHead>Risk</TableHead><TableHead>Confidence</TableHead><TableHead>Source</TableHead><TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.id} className="cursor-pointer" onClick={() => { setSelected(r); setEdited(r.statement); }}>
                  <TableCell className="whitespace-nowrap font-mono text-xs">{r.req_id}</TableCell>
                  <TableCell className="min-w-[320px] max-w-[520px] whitespace-normal text-sm">{r.statement}</TableCell>
                  <TableCell className="min-w-[180px]"><Chips items={r.categories.filter((c) => c !== "functional" && c !== "non_functional")} max={3} /></TableCell>
                  <TableCell><SeverityBadge severity={r.priority === "High" ? "High" : r.priority === "Medium" ? "Medium" : "Low"} /></TableCell>
                  <TableCell><SeverityBadge severity={r.risk_level} /></TableCell>
                  <TableCell className="w-28"><Meter value={r.confidence} /></TableCell>
                  <TableCell className="whitespace-nowrap text-xs">{r.source}</TableCell>
                  <TableCell><StatusPill status={r.status} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      <Sheet open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle className="font-mono">{selected.req_id}</SheetTitle>
                <SheetDescription>{label(selected.kind)} · source: {selected.source}</SheetDescription>
              </SheetHeader>
              <div className="space-y-5 px-4 pb-8 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill status={selected.status} /><GeneratedBy value={selected.generated_by} />
                  <span className="text-xs text-muted-foreground">Priority</span><SeverityBadge severity={selected.priority} />
                  <span className="text-xs text-muted-foreground">Risk</span><SeverityBadge severity={selected.risk_level} />
                </div>
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Requirement statement</p><p className="text-base">{selected.statement}</p></div>
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Original stakeholder text (preserved)</p>
                  <p className="rounded-md bg-muted p-2 font-mono text-xs">{selected.original_text}</p></div>
                {selected.supplementary?.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted-foreground">Supplementary information (added after submission - the original text above is unchanged)</p>
                    <ul className="space-y-1">
                      {selected.supplementary.map((s, i) => (
                        <li key={i} className="rounded-md border border-violet-200 bg-violet-50/60 px-2 py-1 text-xs">
                          <span className="mr-1 font-mono font-medium">{s.ref}</span>
                          <span className="mr-1 text-muted-foreground">({label(s.kind)})</span>{s.text}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Categories</p><Chips items={selected.categories} max={20} /></div>
                {(selected.citations?.length ?? 0) > 0 && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">Domain terms (financial-services glossary used by the Classification Agent)</p>
                    <Evidence citations={selected.citations} className="mt-1" />
                  </div>
                )}
                <div className="grid grid-cols-2 gap-4">
                  <div><p className="mb-1 text-xs font-medium text-muted-foreground">Confidence</p><Meter value={selected.confidence} /></div>
                  <div><p className="mb-1 text-xs font-medium text-muted-foreground">Dependencies</p><ReqIds ids={selected.dependencies} /></div>
                </div>
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">User story</p><p>{selected.user_story}</p></div>
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Acceptance criteria</p><BulletList items={selected.acceptance_criteria} /></div>
                <div><p className="mb-1 text-xs font-medium text-muted-foreground">Test recommendations</p><BulletList items={selected.test_recommendations} /></div>
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Findings ({findings.length})</p>
                  {findings.length ? (
                    <ul className="space-y-2">{findings.map((f) => (
                      <li key={f.id} className="rounded-md border p-2"><div className="flex items-center gap-2"><SeverityBadge severity={f.severity} /><span className="font-medium">{label(f.issue_type)}</span></div>
                        <p className="mt-1 text-xs text-muted-foreground">{f.explanation}</p>{f.suggestion && <p className="mt-1 text-xs">Suggestion: {f.suggestion}</p>}</li>))}</ul>
                  ) : <p className="text-muted-foreground">No issues found.</p>}
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">Compliance mappings ({mappings.length})</p>
                  {mappings.length ? (
                    <ul className="space-y-2">{mappings.map((m) => (
                      <li key={m.id} className="rounded-md border p-2"><div className="flex flex-wrap items-center gap-2"><span className="font-medium">{m.document_name} · {m.control_ref}</span><AuthenticityBadge value={m.authenticity} /><StatusPill status={m.status} /></div>
                        <p className="mt-1 text-xs italic text-muted-foreground">“{m.evidence_text}”</p></li>))}</ul>
                  ) : <p className="text-muted-foreground">No mapped controls.</p>}
                </div>

                <div className="rounded-lg border p-3">
                  <p className="mb-2 font-medium">Human review</p>
                  {!canReview(selected) && <Notice tone="warning">Your role cannot review this requirement.</Notice>}
                  <div className="flex flex-wrap gap-2">
                    <ReviewDialog title={`Accept ${selected.req_id}`} confirmLabel="Accept" disabled={!canReview(selected)}
                      trigger={<Button size="sm" disabled={!canReview(selected)}><Check className="size-4" /> Accept</Button>}
                      onSubmit={(c) => review(selected, "accept", c)} />
                    <ReviewDialog title={`Reject ${selected.req_id}`} confirmLabel="Reject" commentRequired disabled={!canReview(selected)}
                      trigger={<Button size="sm" variant="outline" disabled={!canReview(selected)}><X className="size-4" /> Reject</Button>}
                      onSubmit={(c) => review(selected, "reject", c)} />
                    <ReviewDialog title={`Modify ${selected.req_id}`} confirmLabel="Save modification" disabled={!canReview(selected)}
                      description="The original stakeholder text is kept; the previous and new statements are recorded in the approval history."
                      trigger={<Button size="sm" variant="outline" disabled={!canReview(selected)}><Pencil className="size-4" /> Modify</Button>}
                      onSubmit={(c) => review(selected, "modify", c, edited)}>
                      <div className="space-y-2"><Label>New statement</Label><Textarea rows={4} value={edited} onChange={(e) => setEdited(e.target.value)} /></div>
                    </ReviewDialog>
                  </div>
                  {history.length > 0 && (
                    <ul className="mt-3 space-y-1 border-t pt-2 text-xs text-muted-foreground">
                      {history.map((h) => <li key={h.id}>{formatDate(h.timestamp)} · {h.reviewer_email} ({label(h.reviewer_role)}) · <strong>{h.action}</strong>{h.comment && ` – ${h.comment}`}</li>)}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
