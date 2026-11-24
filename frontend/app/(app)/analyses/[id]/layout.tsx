"use client";

import { FileDown, FilePlus2 } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AnalysisProvider, useAnalysis } from "@/components/analysis-context";
import { EmptyState, ErrorState, LoadingState, Notice, StatusPill } from "@/components/common";
import { ProgressPanel } from "@/components/progress-panel";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import { formatDate, isDone } from "@/lib/format";
import type { AnalysisBrief } from "@/lib/types";
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
        {items.map((a) => <SelectItem key={a.id} value={String(a.id)}>#{a.id} · {a.project_name}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}

function AnalysisChrome({ children }: { children: React.ReactNode }) {
  const { analysis, loading, error, reload } = useAnalysis();
  const pathname = usePathname();
  const section = pathname.split("/")[3] ?? "";
  const onFinished = useCallback(() => { reload(); }, [reload]);

  if (loading) return <LoadingState text="Loading analysis…" />;
  if (error) {
    return error.includes("not found")
      ? <EmptyState title="No completed analysis yet" description="Run your first analysis to see results here."
          action={<Button asChild><Link href="/analyse"><FilePlus2 className="size-4" /> Analyse Requirements</Link></Button>} />
      : <ErrorState message={error} retry={reload} />;
  }
  if (!analysis) return null;
  if (analysis.status === "queued" || analysis.status === "running") {
    return <div className="mx-auto max-w-4xl"><ProgressPanel analysisId={analysis.id} onFinished={onFinished} /></div>;
  }

  return (
    <div>
      <div className="mb-5 flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{analysis.project.name}</h1>
            <StatusPill status={analysis.status} />
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Analysis #{analysis.id} · {analysis.project.domain_label} · {analysis.project.jurisdiction} · {formatDate(analysis.finished_at)} · by {analysis.created_by}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
      {analysis.status === "failed" && (
        <div className="mb-4"><Notice tone="danger">The analysis failed: {analysis.error}</Notice></div>
      )}
      <nav className="mb-6 flex gap-1 overflow-x-auto border-b">
        {SECTIONS.map(([key, text]) => (
          <Link key={key} href={`/analyses/${analysis.id}${key ? `/${key}` : ""}`}
            className={cn("whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors",
              section === key ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {text}
          </Link>
        ))}
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
