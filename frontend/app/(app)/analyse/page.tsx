"use client";

import { ChevronDown, FileText, FileUp, Info, Loader2, Sparkles, Upload, X } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { Notice, PageHeader } from "@/components/common";
import { ProgressPanel } from "@/components/progress-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import type { AnalysisBrief, Project } from "@/lib/types";

const ALLOWED = [".txt", ".md", ".csv", ".pdf", ".docx"];
const MAX_MB = 10;

type Example = { key: string; title: string; project: Partial<Project>; requirements_text: string; requirement_lines: number };
type Form = {
  name: string; domain: string; description: string; jurisdiction: string; scale: string;
  budget: string; schedule: string; stakeholder_availability: string;
};
const EMPTY: Form = { name: "", domain: "", description: "", jurisdiction: "India", scale: "medium", budget: "", schedule: "", stakeholder_availability: "" };

function AnalysePage() {
  const router = useRouter();
  const params = useSearchParams();
  const { can } = useAuth();
  const fileInput = useRef<HTMLInputElement>(null);
  const [domains, setDomains] = useState<{ key: string; label: string }[]>([]);
  const [examples, setExamples] = useState<Example[]>([]);
  const [existing, setExisting] = useState<Project | null>(null);
  const [form, setForm] = useState<Form>(EMPTY);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [analysisId, setAnalysisId] = useState<number | null>(null);

  useEffect(() => {
    api<{ key: string; label: string }[]>("/api/domains").then(setDomains).catch(() => {});
    api<Example[]>("/api/examples").then(setExamples).catch(() => {});
    const projectId = params.get("project");
    if (projectId) api<Project>(`/api/projects/${projectId}`).then(setExisting).catch(() => toast.error("Project not found"));
  }, [params]);

  const set = (key: keyof Form) => (value: string) => setForm((f) => ({ ...f, [key]: value }));
  const lineCount = useMemo(() => text.split("\n").filter((l) => l.trim()).length, [text]);

  const loadExample = (key: string) => {
    const example = examples.find((e) => e.key === key);
    if (!example) return;
    setExisting(null);
    setForm({ ...EMPTY, ...Object.fromEntries(Object.entries(example.project).map(([k, v]) => [k, String(v ?? "")])) } as Form);
    setText(example.requirements_text);
    toast.success(`Loaded example: ${example.title}`, { description: "Synthetic demonstration requirements" });
  };

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const accepted: File[] = [];
    for (const file of Array.from(list)) {
      const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      if (!ALLOWED.includes(ext)) toast.error(`${file.name}: unsupported type (${ALLOWED.join(", ")})`);
      else if (file.size > MAX_MB * 1024 * 1024) toast.error(`${file.name}: larger than ${MAX_MB} MB`);
      else accepted.push(file);
    }
    setFiles((current) => [...current, ...accepted].slice(0, 10));
  };

  const validate = () => {
    const problems: string[] = [];
    if (!existing) {
      if (form.name.trim().length < 2) problems.push("Enter a project name.");
      if (!form.domain) problems.push("Select the financial application domain.");
    }
    if (!text.trim() && files.length === 0) problems.push("Paste requirements or upload at least one requirements document.");
    setErrors(problems);
    return problems.length === 0;
  };

  const analyse = async () => {
    if (!validate()) return;
    setSubmitting(true);
    const data = new FormData();
    if (existing) data.append("project_id", String(existing.id));
    else Object.entries(form).forEach(([k, v]) => data.append(k, v));
    data.append("requirements_text", text);
    files.forEach((file) => data.append("files", file));
    try {
      const result = await api<{ analysis_id: number }>("/api/analyses", { form: data });
      setAnalysisId(result.analysis_id);
    } catch (e) {
      toast.error(errorMessage(e));
      setSubmitting(false);
    }
  };

  const finished = useCallback((status: AnalysisBrief) => {
    if (status.status === "failed") {
      toast.error("The analysis failed", { description: status.error });
      setSubmitting(false);
      return;
    }
    toast.success("Analysis complete");
    router.push(`/analyses/${status.id}`);
  }, [router]);

  if (analysisId) {
    return (
      <div className="mx-auto max-w-4xl">
        <PageHeader title="Analysing requirements" description={`${existing?.name ?? form.name} · the agents run automatically; you will be taken to the results when they finish.`} />
        <ProgressPanel analysisId={analysisId} onFinished={finished} />
        {!submitting && (
          <Button variant="outline" className="mt-4" onClick={() => { setAnalysisId(null); }}>Back to the form</Button>
        )}
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Analyse Requirements"
        description="Describe the financial application, paste or upload its requirements, and click one button. ReqPilot's agents extract, classify, check, map, score and generate the SDLC workflow without asking you anything."
        actions={examples.length > 0 && (
          // Non-modal menu: a modal Select left <body> with pointer-events:none after loading an example,
          // which swallowed the next click (e.g. on ANALYSE REQUIREMENTS).
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              <Button variant="outline"><Sparkles className="size-4" /> Load an example requirement set <ChevronDown className="size-4" /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-[300px]">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Synthetic demonstration requirement sets</DropdownMenuLabel>
              {examples.map((e) => (
                <DropdownMenuItem key={e.key} onSelect={() => loadExample(e.key)}>{e.title} ({e.requirement_lines} lines)</DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      />
      {!can("analysis:run") && (
        <div className="mb-4"><Notice tone="warning">Your role cannot start analyses. Sign in as a Business Analyst, Project Manager or Administrator.</Notice></div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">1. Financial application</CardTitle>
              <CardDescription>{existing ? "A new analysis will be added to this existing project." : "Project context used by the compliance, risk and SDLC agents."}</CardDescription>
            </CardHeader>
            <CardContent>
              {existing ? (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/40 p-4">
                  <div>
                    <p className="font-medium">{existing.name}</p>
                    <p className="text-sm text-muted-foreground">{existing.domain_label} · {existing.jurisdiction} · {existing.scale}</p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => { setExisting(null); router.replace("/analyse"); }}>Use a new project instead</Button>
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="name">Project name *</Label>
                    <Input id="name" value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Digital Banking Application" maxLength={200} />
                  </div>
                  <div className="space-y-2">
                    <Label>Financial domain *</Label>
                    <Select value={form.domain} onValueChange={set("domain")}>
                      <SelectTrigger className="w-full"><SelectValue placeholder="Select a domain" /></SelectTrigger>
                      <SelectContent>{domains.map((d) => <SelectItem key={d.key} value={d.key}>{d.label}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <Label htmlFor="description">Project description</Label>
                    <Textarea id="description" rows={3} value={form.description} onChange={(e) => set("description")(e.target.value)}
                      placeholder="Business context, channels, integrations, regulatory expectations, release cadence…" maxLength={5000} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="jurisdiction">Jurisdiction</Label>
                    <Input id="jurisdiction" value={form.jurisdiction} onChange={(e) => set("jurisdiction")(e.target.value)} maxLength={60} />
                  </div>
                  <div className="space-y-2">
                    <Label>Expected scale</Label>
                    <Select value={form.scale} onValueChange={set("scale")}>
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {["small", "medium", "large", "enterprise"].map((s) => <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="budget">Budget (optional)</Label>
                    <Input id="budget" value={form.budget} onChange={(e) => set("budget")(e.target.value)} placeholder="e.g. INR 6 crore, fixed" maxLength={200} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="schedule">Schedule (optional)</Label>
                    <Input id="schedule" value={form.schedule} onChange={(e) => set("schedule")(e.target.value)} placeholder="e.g. go-live in 9 months" maxLength={200} />
                  </div>
                  <div className="space-y-2">
                    <Label>Stakeholder availability (optional)</Label>
                    <Select value={form.stakeholder_availability || "unknown"} onValueChange={(v) => set("stakeholder_availability")(v === "unknown" ? "" : v)}>
                      <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="unknown">Not specified (assumed moderate)</SelectItem>
                        {["low", "medium", "high", "very high"].map((s) => <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">2. Requirements</CardTitle>
              <CardDescription>
                Paste the complete list - one requirement per line works best. Optional stakeholder tags such as
                <code className="mx-1 rounded bg-muted px-1">[Compliance Officer]</code>are used as the requirement source. Your original text is preserved.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={16} spellCheck={false}
                  className="font-mono text-xs leading-relaxed"
                  placeholder={"[Customer] The app shall allow customers to transfer funds using UPI and IMPS.\n[Security Team] Fund transfers above INR 50,000 shall require step-up authentication.\n[Compliance Officer] All payment data shall be stored only in India.\n…"} />
                <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
                  <span>{lineCount} non-empty lines</span>
                  <span>{text.length.toLocaleString()} characters</span>
                </div>
              </div>

              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}
                className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed bg-muted/30 px-4 py-6 text-center"
              >
                <FileUp className="size-6 text-muted-foreground" />
                <p className="text-sm">Drop requirement documents here or
                  <button type="button" className="ml-1 font-medium text-primary hover:underline" onClick={() => fileInput.current?.click()}>browse</button>
                </p>
                <p className="text-xs text-muted-foreground">TXT, MD, PDF, DOCX or CSV (column “requirement”, optional “source”) · up to {MAX_MB} MB each · combined with the pasted text</p>
                <input ref={fileInput} type="file" multiple accept={ALLOWED.join(",")} className="hidden" onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
              </div>
              {files.length > 0 && (
                <ul className="space-y-2">
                  {files.map((file, i) => (
                    <li key={`${file.name}-${i}`} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                      <span className="flex items-center gap-2"><FileText className="size-4 text-muted-foreground" />{file.name}
                        <Badge variant="secondary" className="font-normal">{(file.size / 1024).toFixed(0)} KB</Badge></span>
                      <Button variant="ghost" size="icon" aria-label={`Remove ${file.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))}><X className="size-4" /></Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {errors.length > 0 && (
            <Notice tone="danger"><ul className="list-disc pl-4">{errors.map((e) => <li key={e}>{e}</li>)}</ul></Notice>
          )}
          <Button size="lg" className="h-12 w-full text-base font-semibold tracking-wide" onClick={analyse} disabled={submitting || !can("analysis:run")}>
            {submitting ? <><Loader2 className="size-5 animate-spin" /> Starting analysis…</> : <><Upload className="size-5" /> ANALYSE REQUIREMENTS</>}
          </Button>
        </div>

        <aside className="space-y-4">
          <Card className="gap-3">
            <CardHeader><CardTitle className="flex items-center gap-2 text-sm"><Info className="size-4 text-primary" /> What happens next</CardTitle></CardHeader>
            <CardContent>
              <ol className="space-y-2 text-sm text-muted-foreground">
                {["Requirements are extracted, normalised and given IDs", "Each requirement is classified (multi-label) and prioritised",
                  "Ambiguity, gaps, duplicates and conflicts are detected", "Regulatory evidence is retrieved and controls are mapped",
                  "Security, privacy and project risks are assessed", "13 project factors are scored to rank 7 SDLC models",
                  "A project-specific SDLC workflow and traceability matrix are generated", "Everything is validated and stored"].map((step, i) => (
                  <li key={step} className="flex gap-2"><span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{i + 1}</span>{step}</li>
                ))}
              </ol>
            </CardContent>
          </Card>
          <Notice>
            ReqPilot never interrupts the analysis with questions. Unclear or conflicting requirements are reported with assumptions and
            clarification questions in the results. Human approval happens afterwards in the Approval Centre.
          </Notice>
          <Notice tone="warning">Use synthetic data only. E-mail, card, Aadhaar, PAN, phone and account numbers are masked before any text is sent to the AI model.</Notice>
        </aside>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <AnalysePage />
    </Suspense>
  );
}
