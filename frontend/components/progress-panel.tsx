"use client";

import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { api } from "@/lib/api";
import { isDone } from "@/lib/format";
import type { AnalysisBrief } from "@/lib/types";
import { cn } from "@/lib/utils";

// Mirrors backend/app/pipeline.py STAGES (agent, user-facing description).
const STAGES = [
  ["Requirement Extraction Agent", "Extracting and normalising requirements"],
  ["Requirement Classification Agent", "Classifying requirements (multi-label)"],
  ["Quality & Conflict Agent", "Detecting ambiguity, duplicates and conflicts"],
  ["Compliance & Regulatory Agent", "Retrieving regulatory evidence and mapping controls"],
  ["Security & Privacy Agent", "Analysing security and privacy coverage"],
  ["Risk Analysis Agent", "Assessing project risks"],
  ["SDLC Selection Agent", "Scoring and ranking SDLC models"],
  ["Workflow Generation Agent", "Generating the project-specific SDLC workflow"],
  ["Documentation Agent", "Writing user stories, acceptance criteria and use cases"],
  ["Traceability Builder", "Building the requirements traceability matrix"],
  ["Validation Agent", "Validating consistency, evidence and completeness"],
  ["Summary Writer", "Writing the executive summary"],
];

/** Polls the analysis status and shows the agent pipeline progress. No user input is ever requested. */
export function ProgressPanel({ analysisId, onFinished }: { analysisId: number | string; onFinished: (status: AnalysisBrief) => void }) {
  const [status, setStatus] = useState<AnalysisBrief | null>(null);

  useEffect(() => {
    let stopped = false;
    const poll = async () => {
      try {
        const s = await api<AnalysisBrief>(`/api/analyses/${analysisId}/status`);
        if (stopped) return;
        setStatus(s);
        if (isDone(s.status) || s.status === "failed") {
          onFinished(s);
          return;
        }
      } catch {
        /* transient - keep polling */
      }
      if (!stopped) setTimeout(poll, 1000);
    };
    poll();
    return () => {
      stopped = true;
    };
  }, [analysisId, onFinished]);

  const progress = status?.progress ?? 0;
  const activeIndex = STAGES.findIndex(([, stage]) => stage === status?.current_stage);
  const failed = status?.status === "failed";

  return (
    <Card>
      <CardContent className="space-y-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">{failed ? "Analysis failed" : isDone(status?.status ?? "") ? "Analysis complete" : "ReqPilot agents are analysing your requirements"}</p>
            <p className="text-xs text-muted-foreground">
              {failed ? status?.error : status?.current_stage ?? "Queued"} · no input is needed while the agents work
            </p>
          </div>
          <span className="text-2xl font-semibold tabular-nums">{progress}%</span>
        </div>
        <Progress value={progress} />
        <ol className="grid gap-2 sm:grid-cols-2">
          {STAGES.map(([agent, stage], index) => {
            const done = isDone(status?.status ?? "") || (activeIndex >= 0 ? index < activeIndex : progress >= ((index + 1) * 100) / STAGES.length);
            const active = index === activeIndex && !done && !failed;
            return (
              <li key={agent} className={cn("flex items-start gap-2 rounded-md border px-3 py-2 text-sm", active && "border-primary/40 bg-primary/5")}>
                {done ? <CheckCircle2 className="mt-0.5 size-4 text-emerald-600" />
                  : active ? <Loader2 className="mt-0.5 size-4 animate-spin text-primary" />
                  : failed && index === activeIndex ? <XCircle className="mt-0.5 size-4 text-red-600" />
                  : <Circle className="mt-0.5 size-4 text-muted-foreground/40" />}
                <div>
                  <p className={cn("font-medium", !done && !active && "text-muted-foreground")}>{agent}</p>
                  <p className="text-xs text-muted-foreground">{stage}</p>
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
