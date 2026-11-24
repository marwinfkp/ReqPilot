"use client";

import { ArrowRight, FilePlus2, FolderKanban, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { EmptyState, ErrorState, LoadingState, PageHeader, Section, StatusPill } from "@/components/common";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { formatDate, label } from "@/lib/format";
import type { Project } from "@/lib/types";

function DeleteProject({ project, onDeleted }: { project: Project; onDeleted: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const remove = async () => {
    setBusy(true);
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      toast.success(`Project "${project.name}" deleted`);
      setOpen(false);
      onDeleted();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Delete ${project.name}`}><Trash2 className="size-4 text-red-600" /></Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete project?</DialogTitle>
          <DialogDescription>
            &ldquo;{project.name}&rdquo; and all {project.analysis_count ?? 0} of its analyses, requirements, findings, approvals and
            workflows will be permanently deleted. This cannot be undone. The action is recorded in the audit log.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="destructive" onClick={remove} disabled={busy}>{busy ? "Deleting…" : "Delete permanently"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ProjectsPage() {
  const { can } = useAuth();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(() => {
    setError(null);
    api<Project[]>("/api/projects").then(setProjects).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!projects || !q) return projects ?? [];
    return projects.filter((p) =>
      [p.name, p.domain_label, p.jurisdiction, p.description, p.owner_email].some((v) => v?.toLowerCase().includes(q)));
  }, [projects, query]);

  const newAnalysis = (
    <Button asChild><Link href="/analyse"><FilePlus2 className="size-4" /> New analysis</Link></Button>
  );

  return (
    <>
      <PageHeader title="Projects" description="Financial applications analysed by ReqPilot. Each project can be analysed several times as requirements evolve." actions={newAnalysis} />
      {error ? <ErrorState message={error} retry={load} /> : !projects ? <LoadingState /> : projects.length === 0 ? (
        <EmptyState icon={FolderKanban} title="No projects yet" description="Projects are created when you submit requirements for analysis." action={newAnalysis} />
      ) : (
        <Section
          title={`${filtered.length} project${filtered.length === 1 ? "" : "s"}`}
          actions={
            <div className="relative w-full max-w-xs">
              <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input className="pl-8" placeholder="Search projects…" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          }
        >
          {filtered.length === 0 ? <p className="py-6 text-center text-sm text-muted-foreground">No project matches &ldquo;{query}&rdquo;.</p> : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Project</TableHead>
                  <TableHead>Domain</TableHead>
                  <TableHead>Jurisdiction</TableHead>
                  <TableHead>Scale</TableHead>
                  <TableHead className="text-right">Analyses</TableHead>
                  <TableHead>Latest status</TableHead>
                  <TableHead>Recommended SDLC</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="max-w-[260px]">
                      <Link href={`/projects/${p.id}`} className="font-medium hover:underline">{p.name}</Link>
                      <p className="truncate text-xs text-muted-foreground">{p.description || "No description"}</p>
                    </TableCell>
                    <TableCell>{p.domain_label}</TableCell>
                    <TableCell>{p.jurisdiction}</TableCell>
                    <TableCell>{label(p.scale)}</TableCell>
                    <TableCell className="text-right tabular-nums">{p.analysis_count ?? 0}</TableCell>
                    <TableCell>{p.latest_analysis ? <StatusPill status={p.latest_analysis.status} /> : "–"}</TableCell>
                    <TableCell>{p.latest_analysis?.recommended_sdlc || "–"}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDate(p.created_at)}</TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      <Button variant="ghost" size="sm" asChild><Link href={`/projects/${p.id}`}>Open <ArrowRight className="size-4" /></Link></Button>
                      {can("project:delete") && <DeleteProject project={p} onDeleted={load} />}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      )}
    </>
  );
}
