"use client";

import { CheckCircle2, FileDown, FilePlus2, GitBranch, Loader2, RotateCcw, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AnalysisProvider, useAnalysis } from "@/components/analysis-context";
import { useAuth } from "@/components/auth";
import { ClarificationCentre } from "@/components/clarification-centre";
import { EmptyState, ErrorState, LoadingState, Notice, StatusPill } from "@/components/common";
import { ProgressPanel } from "@/components/progress-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorMessage } from "@/lib/api";
import { formatDate, isDone } from "@/lib/format";
import type { AnalysisBrief, AnalysisPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

const SECTIONS = [
  ["", "Overview"],
  ["requirements", "Requirements"],
  ["quality", "Quality"],
  ["compliance", "Compliance"],
  ["security", "Security & Risk"],
  ["sdlc", "SDLC Recommendation"],
  ["workflow", "SDLC Workflow"],
  ["traceability", "Traceability"],
  ["issues", "Open Issues"],
];

function AnalysisSwitcher({ currentId, section }: { currentId: number; section: string }) {
  const router = useRouter();
  const [items, setItems] = useState<AnalysisBrief[]>([]);
  useEffect(() => {
    api<AnalysisBrief[]>("/api/analyses").then((list) => setItems(list.filter((a) => isDone(a.status)))).catch(() => {});
  }, []);
  if (items.length < 2) return null;
  return (
    <Select value={String(currentId)} onValueChange={(v) => router.push(`/analyses/${v}${section ? `/${section}` : ""}`)}>
      <SelectTrigger className="w-[260px]"><SelectValue /></SelectTrigger>
      <SelectContent>
        {items.map((a) => <SelectItem key={a.id} value={String(a.id)}>#{a.id} · {a.project_name}{a.version > 1 ? ` (v${a.version})` : ""}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function VersionSelector({ analysis, section }: { analysis: AnalysisPayload; section: string }) {
  const router = useRouter();
  if (analysis.versions.length < 2) return null;
  return (
    <Select value={String(analysis.id)} onValueChange={(v) => router.push(`/analyses/${v}${section ? `/${section}` : ""}`)}>
      <SelectTrigger className="w-[190px]"><GitBranch className="size-4" /><SelectValue /></SelectTrigger>
      <SelectContent>
        {analysis.versions.map((v) => (
          <SelectItem key={v.id} value={String(v.id)} disabled={!isDone(v.status)}>
            Version {v.version}{v.id === analysis.versions[analysis.versions.length - 1].id ? " (latest)" : ""}{!isDone(v.status) ? ` - ${v.status}` : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Stage 1, step 1: the lightweight preliminary analysis that produces the questions. */
function PreliminaryPanel({ analysisId, onDone }: { analysisId: number; onDone: () => void }) {
  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const s = await api<AnalysisBrief>(`/api/analyses/${analysisId}/status`);
        if (stopped) return;
        if (s.status !== "preliminary") { onDone(); return; }
      } catch { /* keep polling */ }
      if (!stopped) setTimeout(poll, 1000);
    };
    poll();
    return () => { stopped = true; };
  }, [analysisId, onDone]);
  return (
    <Card className="mx-auto max-w-3xl">
      <CardContent className="space-y-4 py-6">
        <div className="flex items-center gap-3">
          <Loader2 className="size-5 animate-spin text-primary" />
          <div>
            <p className="font-medium">Preliminary requirements analysis</p>
            <p className="text-sm text-muted-foreground">Looking for ambiguities, conflicts, missing targets and unclear obligations that need clarification…</p>
          </div>
        </div>
        <ul className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
          {["Provisional extraction and classification", "Ambiguity, conflict and completeness checks",
            "Compliance, security and privacy gaps", "Prioritised clarification questions (max 12)"].map((s) => (
            <li key={s} className="flex items-center gap-2"><Search className="size-4" />{s}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AnalysisChrome({ children }: { children: React.ReactNode }) {
  const { analysis, loading, error, reload } = useAnalysis();
  const { can } = useAuth();
  const pathname = usePathname();
  const section = pathname.split("/")[3] ?? "";
  const onFinished = useCallback(() => { reload(); }, [reload]);
  const [retrying, setRetrying] = useState(false);

  if (loading) return <LoadingState text="Loading analysis…" />;
  if (error) {
    return error.includes("not found")
      ? <EmptyState title="No completed analysis yet" description="Run your first analysis to see results here."
          action={<Button asChild><Link href="/analyse"><FilePlus2 className="size-4" /> Analyse Requirements</Link></Button>} />
      : <ErrorState message={error} retry={reload} />;
  }
  if (!analysis) return null;

  if (analysis.status === "preliminary") {
    return (
      <div>
        <h1 className="mb-4 text-2xl font-semibold tracking-tight">{analysis.project.name}</h1>
        <PreliminaryPanel analysisId={analysis.id} onDone={onFinished} />
      </div>
    );
  }
  if (analysis.status === "awaiting_clarification") {
    return (
      <div className="mx-auto max-w-5xl">
        <ClarificationCentre analysisId={analysis.id} stage={1} filter={(q) => q.stage === 1}
          onContinue={async () => {
            await api(`/api/analyses/${analysis.id}/continue`, { method: "POST" });
            toast.success("Full analysis started - all 12 agents are running");
            await reload();
          }}
          header={
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">{analysis.project.name}</span> · Stage 1 of the analysis
                <StatusPill status="awaiting_clarification" text="Awaiting clarification" />
              </div>
              <h1 className="text-2xl font-semibold tracking-tight">Initial Requirements Clarification</h1>
              <p className="max-w-3xl text-sm text-muted-foreground">
                ReqPilot has identified several areas that require clarification before performing the complete requirements analysis.
                Answering these questions will help improve the accuracy of compliance mappings, security assessments, risk analysis,
                SDLC recommendations and workflow generation.
              </p>
              {analysis.error && <Notice tone="warning">{analysis.error}</Notice>}
            </div>
          } />
      </div>
    );
  }
  if (analysis.status === "queued" || analysis.status === "running") {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        {analysis.version > 1 && <Notice>Selective regeneration of version {analysis.version}: only the affected agents re-run.</Notice>}
        <ProgressPanel analysisId={analysis.id} onFinished={onFinished} />
      </div>
    );
  }

  const latest = analysis.versions[analysis.versions.length - 1];
  const isLatest = !latest || latest.id === analysis.id;
  const retry = async () => {
    setRetrying(true);
    try {
      await api(`/api/analyses/${analysis.id}/retry`, { method: "POST" });
      await reload();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div>
      <div className="mb-5 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{analysis.project.name}</h1>
            <StatusPill status={analysis.status} />
            {analysis.version > 1 && <Badge className="bg-[var(--series-1)]">Version {analysis.version}</Badge>}
            {analysis.stage1_status === "completed" && (
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
                <CheckCircle2 className="size-3" /> Stage 1 completed
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Analysis #{analysis.id} · {analysis.project.domain_label} · {analysis.project.jurisdiction} · {formatDate(analysis.finished_at)} · by {analysis.created_by}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <VersionSelector analysis={analysis} section={section} />
          <AnalysisSwitcher currentId={analysis.id} section={section} />
          <Button variant="outline" asChild><Link href={`/reports?analysis=${analysis.id}`}><FileDown className="size-4" /> Export</Link></Button>
        </div>
      </div>
      {analysis.mode === "demo" && (
        <div className="mb-4">
          <Notice tone="warning">
            <strong>Demonstration mode:</strong> no OpenAI API key was configured, so these results come only from ReqPilot&apos;s
            rule-based agents (keyword rules, deterministic scoring and templates). They are not AI-generated output.
          </Notice>
        </div>
      )}
      {!isLatest && (
        <div className="mb-4">
          <Notice>
            You are viewing version {analysis.version}. A newer version (v{latest.version}) was created by Stage 2 refinement —{" "}
            <Link className="font-medium underline" href={`/analyses/${latest.id}${section ? `/${section}` : ""}`}>open the latest version</Link>.
            Earlier versions are preserved unchanged.
          </Notice>
        </div>
      )}
      {analysis.status === "failed" && (
        <div className="mb-4">
          <Notice tone="danger">
            The analysis failed: {analysis.error}
            {analysis.version === 1 && can("analysis:run") && (
              <Button size="sm" variant="outline" className="ml-3" disabled={retrying} onClick={retry}>
                <RotateCcw className="size-4" /> Retry with the saved answers
              </Button>
            )}
          </Notice>
        </div>
      )}
      {isDone(analysis.status) && section !== "refinement" && (
        <Card className="mb-5 border-dashed py-4">
          <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="rounded-lg bg-primary/10 p-2 text-primary"><Sparkles className="size-4" /></span>
              <div>
                <p className="text-sm font-medium">Post-Analysis Refinement <span className="font-normal text-muted-foreground">(optional)</span></p>
                <p className="text-sm text-muted-foreground">
                  Further refine your requirements, resolve outstanding issues, conduct stakeholder interviews and regenerate affected analysis outputs.
                </p>
              </div>
            </div>
            <Button asChild variant={analysis.stage2_status === "not_started" ? "outline" : "default"}>
              <Link href={`/analyses/${analysis.id}/refinement`}>Open Refinement Centre</Link>
            </Button>
          </CardContent>
        </Card>
      )}
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b">
        {SECTIONS.map(([key, text]) => (
          <Link key={key} href={`/analyses/${analysis.id}${key ? `/${key}` : ""}`}
            className={cn("whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors",
              section === key ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {text}
          </Link>
        ))}
        {section === "refinement" && (
          <span className="whitespace-nowrap border-b-2 border-primary px-3 py-2 text-sm font-medium">Refinement Centre</span>
        )}
      </nav>
      {children}
    </div>
  );
}

export default function AnalysisLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>();
  return (
    <AnalysisProvider id={id}>
      <AnalysisChrome>{children}</AnalysisChrome>
    </AnalysisProvider>
  );
}
