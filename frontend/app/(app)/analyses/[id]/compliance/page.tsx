"use client";

import { CheckCircle2, FileSearch, Flag, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth";
import { useLoadedAnalysis } from "@/components/analysis-context";
import { Meter, StackedBars } from "@/components/charts";
import { AuthenticityBadge, EmptyState, GeneratedBy, Notice, ReqIds, Section, StatTile, StatusPill } from "@/components/common";
import { FindingCard } from "@/components/finding-card";
import { ReviewDialog } from "@/components/review-dialog";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { label } from "@/lib/format";

const COVERAGE_CATEGORIES = ["regulatory", "privacy", "security", "data_management", "auditability", "integration"];

export default function CompliancePage() {
  const { analysis: a, reload } = useLoadedAnalysis();
  const { can } = useAuth();
  const [status, setStatus] = useState("all");
  const [authenticity, setAuthenticity] = useState("all");
  const [verification, setVerification] = useState("all");

  const mapped = new Set(a.mappings.map((m) => m.requirement_id));
  const coverage = COVERAGE_CATEGORIES.map((c) => {
    const reqs = a.requirements.filter((r) => r.categories.includes(c));
    const withMapping = reqs.filter((r) => mapped.has(r.req_id)).length;
    return { name: label(c), mapped: withMapping, unmapped: reqs.length - withMapping };
  }).filter((c) => c.mapped + c.unmapped > 0);
  const unmapped = a.requirements.filter((r) => !mapped.has(r.req_id) && r.categories.some((c) => ["regulatory", "privacy", "security"].includes(c)));
  const gaps = a.findings.filter((f) => f.kind === "compliance_gap" || f.issue_type === "knowledge_base_unavailable");
  const rows = a.mappings.filter((m) => (status === "all" || m.status === status)
    && (authenticity === "all" || m.authenticity === authenticity)
    && (verification === "all" || (verification === "yes") === m.needs_verification));
  const reviewMapping = async (id: number, action: string, comment: string) => {
    await api(`/api/mappings/${id}/review`, { body: { action, comment } });
    await reload();
  };

  return (
    <div className="space-y-6">
      <Notice>
        Mappings link requirements to retrieved knowledge-base passages. They are evidence-backed suggestions for a compliance officer,
        not legal determinations. <strong>Illustrative</strong> sources are fictional demo policies; <strong>reference</strong> sources are
        document-level summaries that always need verification; only <strong>official</strong> uploads contain authentic regulatory text.
      </Notice>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="Control mappings" value={a.mappings.length} hint={`${a.metrics.retrieval_method} retrieval`} icon={ShieldCheck} />
        <StatTile label="Requirements mapped" value={mapped.size} hint={`of ${a.requirements.length}`} icon={FileSearch} />
        <StatTile label="Citations verified" value={a.mappings.filter((m) => m.citation_verified).length} hint="quote found in the source" icon={CheckCircle2} tone="good" />
        <StatTile label="Need verification" value={a.mappings.filter((m) => m.needs_verification).length} hint="compliance officer review" icon={TriangleAlert} tone="warning" />
        <StatTile label="Compliance gaps" value={gaps.length} icon={Flag} tone={gaps.length ? "danger" : "good"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Section title="Compliance coverage by category" description="Counted only from recorded requirement-to-control mappings.">
          {coverage.length ? (
            <StackedBars data={coverage} keys={[{ key: "mapped", label: "Mapped to a control", color: "var(--series-1)" },
              { key: "unmapped", label: "No mapping", color: "var(--series-neutral)" }]} />
          ) : <EmptyState title="No compliance-relevant requirements" />}
        </Section>
        <Section title="Applicable regulations and policies" description="Knowledge-base documents that supplied evidence for this project.">
          {a.applicable_sources.length ? (
            <ul className="space-y-3">
              {a.applicable_sources.map((s) => (
                <li key={s.document_id} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{s.name}</span><AuthenticityBadge value={s.authenticity} /></div>
                  <p className="mt-1 text-xs text-muted-foreground">{label(s.doc_type)} · {s.jurisdiction} · version {s.version || "n/a"} · {s.mapped_requirements.length} requirement(s)</p>
                  <div className="mt-2"><ReqIds ids={s.mapped_requirements} max={8} /></div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No sources matched" description="Upload official documents in the Knowledge Base for evidence-based coverage." />}
        </Section>
      </div>

      <Section title={`Requirement-to-control mappings (${rows.length})`}
        description="Approve correct mappings or flag incorrect interpretations. Only compliance officers and administrators can decide."
        actions={
          <div className="flex flex-wrap gap-2">
            {[[status, setStatus, ["pending", "approved", "flagged"], "All statuses"], [authenticity, setAuthenticity, ["official", "illustrative", "reference"], "All sources"],
              [verification, setVerification, ["yes", "no"], "Verification: any"]].map(([value, setter, options, placeholder]) => (
              <Select key={placeholder as string} value={value as string} onValueChange={setter as (v: string) => void}>
                <SelectTrigger className="w-[150px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{placeholder as string}</SelectItem>
                  {(options as string[]).map((o) => <SelectItem key={o} value={o}>{placeholder === "Verification: any" ? `Needs verification: ${o}` : label(o)}</SelectItem>)}
                </SelectContent>
              </Select>
            ))}
          </div>
        }>
        {rows.length ? (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow><TableHead>Requirement</TableHead><TableHead>Control (source)</TableHead><TableHead>Supporting evidence</TableHead>
                  <TableHead>Confidence</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Review</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="font-mono text-xs">{m.requirement_id}</TableCell>
                    <TableCell className="min-w-[220px] whitespace-normal">
                      <p className="font-medium">{m.control_ref} · {m.control_title}</p>
                      <p className="text-xs text-muted-foreground">{m.document_name} · {m.jurisdiction} · v{m.version || "n/a"}</p>
                      <div className="mt-1 flex flex-wrap gap-1"><AuthenticityBadge value={m.authenticity} /><GeneratedBy value={m.generated_by} /></div>
                    </TableCell>
                    <TableCell className="min-w-[320px] max-w-[460px] whitespace-normal text-xs">
                      <p className="italic">“{m.evidence_text}”</p>
                      <p className="mt-1 text-muted-foreground">{m.rationale}</p>
                      <p className={m.citation_verified ? "mt-1 text-emerald-700" : "mt-1 text-red-700"}>
                        {m.citation_verified ? "✓ Citation verified in source passage" : "✗ Quote not found in source - requires verification"}</p>
                    </TableCell>
                    <TableCell className="w-32"><Meter value={m.confidence} />{m.needs_verification && <p className="mt-1 text-[11px] text-amber-800">Needs verification</p>}</TableCell>
                    <TableCell><StatusPill status={m.status} /></TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <ReviewDialog title="Approve mapping" confirmLabel="Approve" disabled={!can("mapping:review") || m.status === "approved"}
                          description={`${m.requirement_id} → ${m.document_name} ${m.control_ref}`}
                          trigger={<Button size="sm" variant="outline" disabled={!can("mapping:review") || m.status === "approved"}>Approve</Button>}
                          onSubmit={(c) => reviewMapping(m.id, "approve", c)} />
                        <ReviewDialog title="Flag incorrect interpretation" confirmLabel="Flag" commentRequired disabled={!can("mapping:review")}
                          description="Explain why this control does not apply or is misinterpreted."
                          trigger={<Button size="sm" variant="ghost" disabled={!can("mapping:review")}>Flag</Button>}
                          onSubmit={(c) => reviewMapping(m.id, "flag", c)} />
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : <EmptyState title="No mappings match the filters" />}
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title={`Potential compliance gaps (${gaps.length})`} description="Obligations a regulated application in this domain is expected to address.">
          {gaps.length ? <ul className="space-y-3">{gaps.map((f) => <FindingCard key={f.id} f={f} />)}</ul> : <EmptyState title="No gaps detected" />}
        </Section>
        <Section title={`Unmapped regulatory / security / privacy requirements (${unmapped.length})`} description="No knowledge-base passage supported these requirements.">
          {unmapped.length ? (
            <ul className="space-y-2 text-sm">{unmapped.map((r) => <li key={r.id} className="rounded-md border p-2"><span className="mr-2 font-mono text-xs">{r.req_id}</span>{r.statement}</li>)}</ul>
          ) : <EmptyState title="Every such requirement has a mapping" />}
        </Section>
      </div>
    </div>
  );
}
