"use client";

import { AlertTriangle, Library, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { AuthenticityBadge, Chips, EmptyState, ErrorState, LoadingState, Notice, PageHeader, Section } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, errorMessage } from "@/lib/api";
import { formatDate, label } from "@/lib/format";

type KbDocument = {
  id: number;
  name: string;
  source_organisation: string;
  jurisdiction: string;
  version: string;
  effective_date: string;
  doc_type: string;
  authenticity: string;
  applicability: string;
  filename: string;
  allowlisted: boolean;
  warnings: string[];
  uploaded_by: string;
  upload_date: string;
  chunk_count: number;
  embedded_chunks: number;
  suspicious_chunks: number;
};

type Chunk = { id: number; section_ref: string; title: string; text: string; topics: string[]; suspicious: boolean; embedded: boolean; page?: number | null };

type SearchHit = {
  chunk_id: number; document_name: string; section_ref: string; title: string; text: string; score: number; authenticity: string;
  doc_type?: string; page?: number | null; match?: string;
};

const DOC_TYPES = ["regulation", "organisational_policy", "security_standard", "reference", "engineering_guideline", "domain_glossary"];
// Test retrieval with the same document-type filter an agent uses (backend rag.AGENT_PROFILES).
const RETRIEVAL_AGENTS: [string, string][] = [
  ["", "All documents"], ["compliance", "Compliance & Regulatory"], ["security", "Security & Privacy"], ["risk", "Risk Analysis"],
  ["classification", "Classification (glossary)"], ["quality", "Quality (requirements guideline)"], ["sdlc", "SDLC Selection"],
  ["workflow", "Workflow Generation"], ["documentation", "Documentation"], ["validation", "Validation (testing standard)"],
  ["clarification", "Clarification questions"],
];
const AUTHENTICITY = ["official", "illustrative", "reference"];

function DocumentSheet({ documentId, onClose }: { documentId: number | null; onClose: () => void }) {
  const [doc, setDoc] = useState<(KbDocument & { chunks: Chunk[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (documentId === null) return;
    setDoc(null);
    setError(null);
    api<KbDocument & { chunks: Chunk[] }>(`/api/knowledge/documents/${documentId}`).then(setDoc).catch((e) => setError(errorMessage(e)));
  }, [documentId]);

  return (
    <Sheet open={documentId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>{doc?.name ?? "Document"}</SheetTitle>
          <SheetDescription>
            {doc ? `${doc.source_organisation} · ${doc.jurisdiction} · version ${doc.version || "–"} · ${doc.chunk_count} chunks` : "Loading…"}
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-3 px-4 pb-6">
          {error && <ErrorState message={error} />}
          {!doc && !error && <LoadingState />}
          {doc && (
            <>
              <div className="flex flex-wrap gap-2">
                <AuthenticityBadge value={doc.authenticity} />
                <Badge variant="outline">{label(doc.doc_type)}</Badge>
                <Badge variant={doc.allowlisted ? "secondary" : "outline"}>{doc.allowlisted ? "Allowlisted for retrieval" : "Not allowlisted"}</Badge>
              </div>
              {doc.applicability && <p className="text-sm text-muted-foreground">Applicability: {doc.applicability}</p>}
              {doc.chunks.map((chunk) => (
                <div key={chunk.id} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-semibold">{chunk.section_ref}</span>
                    <span className="text-sm font-medium">{chunk.title}</span>
                    {chunk.suspicious && (
                      <span className="inline-flex items-center gap-1 rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] text-red-800">
                        <AlertTriangle className="size-3" /> Instruction-like text · excluded from retrieval
                      </span>
                    )}
                    {chunk.page ? <span className="text-[11px] text-muted-foreground">page {chunk.page}</span> : null}
                    {!chunk.embedded && <span className="text-[11px] text-muted-foreground">no embedding</span>}
                  </div>
                  <div className="mt-1"><Chips items={chunk.topics} max={5} /></div>
                  <p className="mt-2 whitespace-pre-line text-xs leading-relaxed text-muted-foreground">{chunk.text}</p>
                </div>
              ))}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function UploadDialog({ canManage, onUploaded }: { canManage: boolean; onUploaded: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({
    name: "", source_organisation: "", jurisdiction: "India", version: "", effective_date: "",
    doc_type: "organisational_policy", authenticity: "illustrative", applicability: "",
  });
  const set = (key: keyof typeof form) => (value: string) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const data = new FormData();
      data.append("file", file);
      Object.entries(form).forEach(([k, v]) => data.append(k, v));
      const doc = await api<KbDocument>("/api/knowledge/documents", { form: data });
      toast.success(`Uploaded "${doc.name}" (${doc.chunk_count} chunks)${doc.allowlisted ? "" : " - awaiting allowlisting"}`);
      setOpen(false);
      setFile(null);
      onUploaded();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button><Upload className="size-4" /> Upload document</Button></DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Upload a knowledge-base document</DialogTitle>
          <DialogDescription>
            TXT, MD, PDF or DOCX (max 10 MB). The text is chunked, tagged with compliance topics and embedded when OpenAI is configured.
            {canManage ? " As a knowledge-base manager your upload is allowlisted immediately."
              : " Your upload will NOT be used for retrieval until a Compliance Officer or Administrator allowlists it."}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="kb-file">File</Label>
            <Input id="kb-file" type="file" accept=".txt,.md,.pdf,.docx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="kb-name">Document name</Label>
            <Input id="kb-name" value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Defaults to the file name" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kb-org">Source organisation *</Label>
            <Input id="kb-org" value={form.source_organisation} onChange={(e) => set("source_organisation")(e.target.value)} placeholder="e.g. Reserve Bank of India" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kb-jur">Jurisdiction</Label>
            <Input id="kb-jur" value={form.jurisdiction} onChange={(e) => set("jurisdiction")(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kb-version">Version</Label>
            <Input id="kb-version" value={form.version} onChange={(e) => set("version")(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="kb-date">Effective date</Label>
            <Input id="kb-date" type="date" value={form.effective_date} onChange={(e) => set("effective_date")(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>Document type</Label>
            <Select value={form.doc_type} onValueChange={set("doc_type")}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{DOC_TYPES.map((t) => <SelectItem key={t} value={t}>{label(t)}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label>Authenticity</Label>
            <Select value={form.authenticity} onValueChange={set("authenticity")}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>{AUTHENTICITY.map((t) => <SelectItem key={t} value={t}>{label(t)}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="kb-app">Applicability</Label>
            <Textarea id="kb-app" rows={2} value={form.applicability} onChange={(e) => set("applicability")(e.target.value)}
              placeholder="Which applications or entities this document applies to" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={submit} disabled={busy || !file || !form.source_organisation.trim()}>{busy ? "Uploading…" : "Upload"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDocument({ doc, onDeleted }: { doc: KbDocument; onDeleted: () => void }) {
  const [open, setOpen] = useState(false);
  const remove = async () => {
    try {
      await api(`/api/knowledge/documents/${doc.id}`, { method: "DELETE" });
      toast.success(`Deleted "${doc.name}"`);
      setOpen(false);
      onDeleted();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`Delete ${doc.name}`}><Trash2 className="size-4 text-red-600" /></Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete document?</DialogTitle>
          <DialogDescription>
            &ldquo;{doc.name}&rdquo; and its {doc.chunk_count} chunks will be permanently removed from the knowledge base. Existing
            analyses keep their recorded mappings.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="destructive" onClick={remove}>Delete permanently</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function KnowledgePage() {
  const { can } = useAuth();
  const canManage = can("kb:manage");
  const [docs, setDocs] = useState<KbDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<{ method: string; results: SearchHit[]; doc_types?: string[] | null } | null>(null);
  const [agent, setAgent] = useState("");
  const [reindexing, setReindexing] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api<KbDocument[]>("/api/knowledge/documents").then(setDocs).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const toggle = async (doc: KbDocument, allowlisted: boolean) => {
    try {
      await api(`/api/knowledge/documents/${doc.id}`, { method: "PATCH", body: { allowlisted } });
      toast.success(`${doc.name} ${allowlisted ? "allowlisted for retrieval" : "removed from retrieval"}`);
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try {
      setResults(await api(`/api/knowledge/search?q=${encodeURIComponent(query)}&jurisdiction=India&k=5${agent ? `&agent=${agent}` : ""}`));
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSearching(false);
    }
  };

  const reindex = async () => {
    setReindexing(true);
    try {
      const r = await api<{ embedded: number }>("/api/knowledge/reindex", { method: "POST" });
      toast.success(`Embedded ${r.embedded} chunk(s)`);
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setReindexing(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        description="Regulations, policies, security standards, engineering guidelines and a domain glossary. Each agent retrieves the passages relevant to its own task."
        actions={
          <>
            {canManage && (
              <Button variant="outline" onClick={reindex} disabled={reindexing}>
                <RefreshCw className={`size-4 ${reindexing ? "animate-spin" : ""}`} /> Re-index embeddings
              </Button>
            )}
            {can("kb:upload") && <UploadDialog canManage={canManage} onUploaded={load} />}
          </>
        }
      />
      <div className="mb-6 space-y-3">
        <Notice tone="warning">
          <strong>Illustrative</strong> documents are fictional &ldquo;Demo Bank&rdquo; policies written for this prototype - they are not laws.
          {" "}<strong>Reference</strong> entries only summarise real regulations at document level (no regulatory text) and always need
          verification. <strong>Official</strong> documents are uploaded by authorised users. Mappings show which kind of evidence they rest on.
        </Notice>
        <Notice>
          Retrieved passages are treated as untrusted data: they are wrapped as data in every prompt, sections containing instruction-like
          text are excluded, and only allowlisted documents of the project&apos;s jurisdiction are retrieved. Every agent searches
          only its own document types (e.g. the Quality Agent the requirements guideline, the Security Agent policies and standards),
          and every citation is checked against the passage it was given.
        </Notice>
      </div>

      <div className="space-y-6">
        <Section title="Test retrieval" description="Runs the same retrieval the agents use (semantic with pgvector when embeddings exist, otherwise lexical), optionally with one agent's document-type filter.">
          <form onSubmit={search} className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input className="pl-8" value={query} onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. step-up authentication for high-value fund transfers" />
            </div>
            <Select value={agent || "all"} onValueChange={(v) => setAgent(v === "all" ? "" : v)}>
              <SelectTrigger className="sm:w-60" aria-label="Retrieve as agent"><SelectValue /></SelectTrigger>
              <SelectContent>{RETRIEVAL_AGENTS.map(([key, name]) => <SelectItem key={key || "all"} value={key || "all"}>{name}</SelectItem>)}</SelectContent>
            </Select>
            <Button type="submit" disabled={searching || !query.trim()}>{searching ? "Searching…" : "Search"}</Button>
          </form>
          {results && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-muted-foreground">Method: <span className="font-medium text-foreground">{results.method}</span> · {results.results.length} result(s)
                {results.doc_types && <> · document types: {results.doc_types.map(label).join(", ")}</>}</p>
              {results.results.length === 0 && <p className="text-sm text-muted-foreground">No allowlisted passages matched.</p>}
              {results.results.map((hit) => (
                <div key={hit.chunk_id} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium">{hit.document_name}</span>
                    <span className="font-mono text-xs">{hit.section_ref}</span>
                    <span className="text-muted-foreground">{hit.title}</span>
                    <AuthenticityBadge value={hit.authenticity} />
                    {hit.doc_type && <Badge variant="outline" className="font-normal">{label(hit.doc_type)}</Badge>}
                    {hit.page ? <span className="text-xs text-muted-foreground">page {hit.page}</span> : null}
                    {hit.match && <span className="text-xs text-primary">{hit.match}</span>}
                    <span className="ml-auto text-xs tabular-nums text-muted-foreground">score {hit.score.toFixed(3)}</span>
                  </div>
                  <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">{hit.text}</p>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="Documents" description="Click a document to inspect its chunks, topics and prompt-injection flags.">
          {error ? <ErrorState message={error} retry={load} /> : !docs ? <LoadingState /> : docs.length === 0 ? (
            <EmptyState icon={Library} title="Knowledge base is empty"
              description="Without documents, compliance analysis is limited to gap detection and every mapping requires verification." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Document</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Authenticity</TableHead>
                  <TableHead>Jurisdiction</TableHead>
                  <TableHead>Version / effective</TableHead>
                  <TableHead className="text-right">Chunks</TableHead>
                  <TableHead className="text-right">Embedded</TableHead>
                  <TableHead>Allowlisted</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {docs.map((doc) => (
                  <TableRow key={doc.id}>
                    <TableCell className="max-w-[300px]">
                      <button type="button" onClick={() => setOpenDoc(doc.id)} className="text-left font-medium hover:underline">{doc.name}</button>
                      <p className="text-xs text-muted-foreground">{doc.source_organisation} · uploaded {formatDate(doc.upload_date)} by {doc.uploaded_by}</p>
                      {doc.warnings?.map((w, i) => (
                        <p key={i} className="mt-1 flex items-start gap-1 text-xs text-amber-800"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{w}</p>
                      ))}
                    </TableCell>
                    <TableCell className="text-xs">{label(doc.doc_type)}</TableCell>
                    <TableCell><AuthenticityBadge value={doc.authenticity} /></TableCell>
                    <TableCell>{doc.jurisdiction}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs">{doc.version || "–"} · {doc.effective_date || "–"}</TableCell>
                    <TableCell className="text-right tabular-nums">{doc.chunk_count}</TableCell>
                    <TableCell className="text-right tabular-nums">{doc.embedded_chunks}</TableCell>
                    <TableCell>
                      <label className="flex items-center gap-2 text-xs">
                        <Checkbox checked={doc.allowlisted} disabled={!canManage} onCheckedChange={(v) => toggle(doc, v === true)}
                          aria-label={`Allowlist ${doc.name}`} />
                        {doc.allowlisted ? "Yes" : "No"}
                      </label>
                    </TableCell>
                    <TableCell className="text-right">{canManage && <DeleteDocument doc={doc} onDeleted={load} />}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {!canManage && docs && docs.length > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">Only Compliance Officers and Administrators can change allowlisting or delete documents.</p>
          )}
        </Section>
      </div>
      <DocumentSheet documentId={openDoc} onClose={() => setOpenDoc(null)} />
    </>
  );
}
