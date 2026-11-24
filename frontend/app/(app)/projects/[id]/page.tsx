"use client";

import { ArrowLeft, ArrowRight, FilePlus2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState, PageHeader, Section, StatusPill } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDate, label } from "@/lib/format";
import type { Project } from "@/lib/types";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<Project>(`/api/projects/${id}`).then(setProject).catch((e) => setError(errorMessage(e)));
  }, [id]);
  useEffect(load, [load]);

  const back = <Button variant="outline" asChild><Link href="/projects"><ArrowLeft className="size-4" /> Projects</Link></Button>;
  if (error) return <><PageHeader title="Project" actions={back} /><ErrorState message={error} retry={load} /></>;
  if (!project) return <LoadingState />;

  const runNew = (
    <Button asChild><Link href={`/analyse?project=${project.id}`}><FilePlus2 className="size-4" /> Run new analysis</Link></Button>
  );
  const details: [string, string][] = [
    ["Domain", project.domain_label],
    ["Jurisdiction", project.jurisdiction],
    ["Expected scale", label(project.scale)],
    ["Budget", project.budget || "Not provided"],
    ["Schedule", project.schedule || "Not provided"],
    ["Stakeholder availability", project.stakeholder_availability ? label(project.stakeholder_availability) : "Not provided"],
    ["Owner", project.owner_email],
    ["Created", formatDate(project.created_at)],
  ];

  return (
    <>
      <PageHeader title={project.name} description={project.description || "No description provided."} actions={<>{back}{runNew}</>} />
      <div className="grid gap-6 lg:grid-cols-3">
        <Section title="Project details">
          <dl className="divide-y text-sm">
            {details.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4 py-2">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="text-right font-medium">{v}</dd>
              </div>
            ))}
          </dl>
        </Section>
        <Section className="lg:col-span-2" title="Analysis history"
          description="Each run preserves the original submission and its own results, approvals and audit trail.">
          {!project.analyses?.length ? (
            <EmptyState title="No analyses yet" description="Run the agent pipeline on this project's requirements." action={runNew} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Recommended SDLC</TableHead>
                  <TableHead>Run by</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {project.analyses.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell className="font-mono text-xs">{a.id}</TableCell>
                    <TableCell><StatusPill status={a.status} /></TableCell>
                    <TableCell className="text-xs">{a.mode === "openai" ? `OpenAI · ${a.model_name}` : "Demo (rules)"}</TableCell>
                    <TableCell>{a.recommended_sdlc || "–"}</TableCell>
                    <TableCell className="text-xs">{a.created_by}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDate(a.started_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" asChild><Link href={`/analyses/${a.id}`}>Results <ArrowRight className="size-4" /></Link></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </div>
    </>
  );
}
