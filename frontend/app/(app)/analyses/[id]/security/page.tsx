"use client";

import { Fingerprint, Lock, ShieldAlert, ShieldX, TriangleAlert } from "lucide-react";

import { useLoadedAnalysis } from "@/components/analysis-context";
import { useAuth } from "@/components/auth";
import { ReviewDialog } from "@/components/review-dialog";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { ColumnBars, RiskMatrix, SeverityBars } from "@/components/charts";
import { EmptyState, GeneratedBy, ReqIds, Section, SeverityBadge, StatTile, StatusPill } from "@/components/common";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { label } from "@/lib/format";

export default function SecurityRiskPage() {
  const { analysis: a, reload } = useLoadedAnalysis();
  const { can } = useAuth();
  const securityReqs = a.requirements.filter((r) => r.categories.includes("security"));
  const privacyReqs = a.requirements.filter((r) => r.categories.includes("privacy"));
  const gaps = a.findings.filter((f) => f.kind === "security_gap");
  const threats = a.findings.filter((f) => f.kind === "threat");
  const privacy = a.findings.filter((f) => f.kind === "privacy");
  const highRisk = [...gaps, ...threats].filter((f) => f.severity === "High" || f.severity === "Critical");
  const severity = a.risks.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.severity]: (acc[r.severity] ?? 0) + 1 }), {});
  const byCategory = Object.entries(a.risks.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.category]: (acc[r.category] ?? 0) + 1 }), {}))
    .map(([name, value]) => ({ name: label(name), value }));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatTile label="Security requirements" value={securityReqs.length} icon={Lock} />
        <StatTile label="Privacy requirements" value={privacyReqs.length} icon={Fingerprint} />
        <StatTile label="Missing / weak controls" value={gaps.length} icon={ShieldX} tone={gaps.length ? "danger" : "good"} />
        <StatTile label="Threats identified" value={threats.length} icon={ShieldAlert} tone="warning" />
        <StatTile label="High / critical risks" value={a.metrics.high_risks} hint={`${a.risks.length} risks registered`} icon={TriangleAlert} tone={a.metrics.high_risks ? "danger" : "good"} />
      </div>

      <Section title="Security coverage" description="Ten security areas checked against the submitted requirements. 'Partial' means only vague requirements cover the area.">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {a.security_coverage.map((area) => (
            <div key={area.area} className="flex flex-col gap-2 rounded-lg border p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">{area.label}</p>
                <StatusPill status={area.status === "partial" ? "partial_coverage" : area.status} text={label(area.status)} />
              </div>
              <ReqIds ids={area.requirement_ids} max={4} />
              {area.status !== "covered" && <p className="text-xs text-muted-foreground"><span className="font-medium text-foreground">Recommended: </span>{area.safeguard}</p>}
            </div>
          ))}
        </div>
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title={`High-risk security issues (${highRisk.length})`} description="High or critical security gaps and threats.">
          {highRisk.length ? (
            <ul className="space-y-3">
              {highRisk.map((f) => (
                <li key={f.id} className="rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2"><SeverityBadge severity={f.severity} /><span className="text-xs uppercase text-muted-foreground">{label(f.kind)} · {f.issue_type}</span><GeneratedBy value={f.generated_by} /></div>
                  <p className="mt-1 font-medium">{f.title}</p>
                  {f.suggestion && <p className="mt-1 text-sm"><span className="font-medium">Safeguard: </span>{f.suggestion}</p>}
                  {f.requirement_ids.length > 0 && <div className="mt-2"><ReqIds ids={f.requirement_ids} /></div>}
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No high-risk security issues" />}
        </Section>
        <Section title={`Privacy considerations (${privacy.length})`}>
          {privacy.length ? (
            <ul className="space-y-3">
              {privacy.map((f) => (
                <li key={f.id} className="rounded-lg border p-3">
                  <div className="flex items-center gap-2"><p className="font-medium">{f.title}</p><GeneratedBy value={f.generated_by} /></div>
                  <p className="mt-1 text-sm text-muted-foreground">{f.explanation}</p>
                  <p className="mt-1 text-sm"><span className="font-medium">Recommendation: </span>{f.suggestion}</p>
                  <div className="mt-2"><ReqIds ids={f.requirement_ids} /></div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No privacy considerations" />}
        </Section>
      </div>

      <Section title={`Threat analysis (${threats.length})`} description="STRIDE categories with the recommended safeguards.">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>STRIDE</TableHead><TableHead>Severity</TableHead><TableHead>Threat</TableHead><TableHead>Safeguard</TableHead><TableHead>Requirements</TableHead></TableRow></TableHeader>
            <TableBody>
              {threats.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="whitespace-nowrap text-xs font-medium">{t.issue_type}</TableCell>
                  <TableCell><SeverityBadge severity={t.severity} /></TableCell>
                  <TableCell className="min-w-[240px] whitespace-normal">{t.title}<p className="text-xs text-muted-foreground">{t.explanation}</p></TableCell>
                  <TableCell className="min-w-[260px] whitespace-normal text-sm">{t.suggestion}</TableCell>
                  <TableCell className="min-w-[160px]"><ReqIds ids={t.requirement_ids} max={4} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section title={`Identified security & privacy requirements (${new Set([...securityReqs, ...privacyReqs]).size})`}>
        <ul className="grid gap-2 md:grid-cols-2">
          {[...new Set([...securityReqs, ...privacyReqs])].map((r) => (
            <li key={r.id} className="flex gap-2 rounded-md border p-2 text-sm"><span className="font-mono text-xs text-muted-foreground">{r.req_id}</span><span className="flex-1">{r.statement}</span><SeverityBadge severity={r.risk_level} /></li>
          ))}
        </ul>
      </Section>

      <h2 className="pt-2 text-lg font-semibold">Risk register</h2>
      <div className="grid gap-6 lg:grid-cols-3">
        <Section title="Risk matrix" description="Likelihood × impact (1–5 each). Hover a cell for its risks.">
          <RiskMatrix risks={a.risks} />
        </Section>
        <Section title="Risks by severity" description="Score = L × I · Low 1–4 · Medium 5–9 · High 10–15 · Critical 16–25">
          <SeverityBars counts={severity} height={250} />
        </Section>
        <Section title="Risks by category">
          <ColumnBars data={byCategory} height={250} />
        </Section>
      </div>
      <Section title={`Risk register (${a.risks.length})`}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Risk</TableHead><TableHead>Category</TableHead><TableHead className="text-center">L</TableHead><TableHead className="text-center">I</TableHead><TableHead className="text-center">Score</TableHead><TableHead>Severity</TableHead><TableHead>Mitigation</TableHead><TableHead>Requirements</TableHead><TableHead>Decision</TableHead></TableRow></TableHeader>
            <TableBody>
              {a.risks.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="font-mono text-xs">{r.risk_id}</TableCell>
                  <TableCell className="min-w-[260px] whitespace-normal">{r.description}<div className="mt-1"><GeneratedBy value={r.generated_by} /></div></TableCell>
                  <TableCell>{label(r.category)}</TableCell>
                  <TableCell className="text-center tabular-nums">{r.likelihood}</TableCell>
                  <TableCell className="text-center tabular-nums">{r.impact}</TableCell>
                  <TableCell className="text-center font-semibold tabular-nums">{r.score}</TableCell>
                  <TableCell><SeverityBadge severity={r.severity} /></TableCell>
                  <TableCell className="min-w-[260px] whitespace-normal text-sm">{r.mitigation}</TableCell>
                  <TableCell className="min-w-[150px]"><ReqIds ids={r.requirement_ids} max={3} /></TableCell>
                  <TableCell className="min-w-[150px]">
                    {r.severity === "High" || r.severity === "Critical" ? (
                      r.status === "open" ? (
                        <ReviewDialog title={`Accept risk ${r.risk_id}`} confirmLabel="Accept risk" commentRequired
                          description="Record a human decision to accept this high/critical risk (with its mitigation). Security risks need a Security Officer."
                          disabled={!can("risk:accept")}
                          trigger={<Button size="sm" variant="outline" disabled={!can("risk:accept")}>Accept risk</Button>}
                          onSubmit={async (comment) => { await api(`/api/risks/${r.id}/review`, { body: { action: "accept", comment } }); await reload(); }} />
                      ) : <StatusPill status={r.status === "accepted" ? "approved" : "rejected"} text={r.status === "accepted" ? "Accepted" : "Rejected"} />
                    ) : <span className="text-xs text-muted-foreground">not required</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>
    </div>
  );
}
