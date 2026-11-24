"use client";

import { Bot, Database, RotateCcw, Save, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { useAuth } from "@/components/auth";
import { ErrorState, LoadingState, Notice, PageHeader, Section, StatusPill } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { ROLE_LABELS, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

type Settings = {
  mode: string;
  openai_api_key_configured: boolean;
  openai_model: string;
  embedding_model: string;
  retention_days: number;
  sdlc_weights: Record<string, number>;
  max_upload_mb: number;
  session_hours: number;
  database: string;
};

type SdlcConfig = {
  models: string[];
  suitable_threshold: number;
  factors: { id: string; name: string; default_weight: number; weight: number; low: string; high: string }[];
  affinity: Record<string, Record<string, number>>;
  rules: { id: string; description: string; penalties: Record<string, number> }[];
  formula: string;
};

type ManagedUser = { id: number; email: string; name: string; role: string; is_active: boolean; created_at: string };

// Read-only mirror of ROLE_PERMISSIONS in backend/app/security.py (the backend enforces it).
const ROLE_PERMISSIONS: [string, string[]][] = [
  ["admin", ["All permissions (*)"]],
  ["business_analyst", ["project:write", "analysis:run", "requirement:review", "baseline:approve", "kb:upload", "evaluation:run",
    "clarification:answer", "change:propose", "change:approve", "stakeholder:record", "stakeholder:review", "regeneration:run"]],
  ["compliance_officer", ["mapping:review", "kb:upload", "kb:manage", "requirement:review_security", "clarification:answer",
    "change:propose", "stakeholder:record"]],
  ["security_officer", ["requirement:review_security", "kb:upload", "clarification:answer", "change:propose", "stakeholder:record",
    "risk:accept"]],
  ["project_manager", ["project:write", "analysis:run", "requirement:review", "baseline:approve", "sdlc:approve", "workflow:approve",
    "evaluation:run", "clarification:answer", "change:propose", "change:approve", "stakeholder:record", "stakeholder:review",
    "regeneration:run", "risk:accept"]],
  ["auditor", ["audit:view"]],
];

// Two-stage analysis permissions (Stage 1 clarification and Stage 2 refinement).
const TWO_STAGE_PERMISSIONS: [string, string][] = [
  ["clarification:answer", "Answer, edit, defer or flag clarification questions (Stage 1 and Stage 2)."],
  ["change:propose", "Propose a requirement change request (the original requirement is never overwritten)."],
  ["change:approve", "Approve, reject or request changes on a change request - never one you proposed yourself (four-eyes)."],
  ["stakeholder:record", "Record a stakeholder interview you conducted (ReqPilot never contacts stakeholders)."],
  ["stakeholder:review", "Accept, reject or request changes on recorded stakeholder interviews."],
  ["regeneration:run", "Confirm a dependency-aware selective regeneration that creates a new analysis version."],
  ["risk:accept", "Formally accept or reject high/critical risks (security risks: Security Officer or Administrator)."],
];

function affinityClass(value: number) {
  if (value >= 2) return "bg-blue-600 text-white";
  if (value === 1) return "bg-blue-100 text-blue-900";
  if (value === -1) return "bg-orange-100 text-orange-900";
  if (value <= -2) return "bg-orange-500 text-white";
  return "text-muted-foreground";
}

function WeightsSection({ config, canManage, onSaved }: { config: SdlcConfig; canManage: boolean; onSaved: () => void }) {
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setWeights(Object.fromEntries(config.factors.map((f) => [f.id, String(f.weight)])));
  }, [config]);

  const save = async (values: Record<string, number>) => {
    setSaving(true);
    try {
      await api("/api/settings", { method: "PUT", body: { sdlc_weights: values } });
      toast.success("SDLC factor weights saved - they apply to new analyses");
      onSaved();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };
  const parsed = Object.fromEntries(Object.entries(weights).map(([k, v]) => [k, Number(v)]));
  const invalid = Object.values(parsed).some((v) => Number.isNaN(v) || v < 0 || v > 5);

  return (
    <Section title="SDLC scoring weights"
      description="How much each project factor counts in the deterministic suitability score. Changes affect future analyses only."
      actions={canManage ? (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={saving}
            onClick={() => save(Object.fromEntries(config.factors.map((f) => [f.id, f.default_weight])))}>
            <RotateCcw className="size-4" /> Defaults
          </Button>
          <Button size="sm" disabled={saving || invalid} onClick={() => save(parsed)}><Save className="size-4" /> Save weights</Button>
        </div>
      ) : <Badge variant="outline">Administrator only</Badge>}>
      <div className="space-y-6">
        <Notice>
          <p className="font-medium">Formula</p>
          <p className="mt-1 font-mono text-xs">{config.formula}</p>
          <p className="mt-2">
            Suitability scores are <strong>not probabilities</strong>: they are the weighted percentage of a model&apos;s preferences that the
            project satisfies. A model is called suitable at ≥ {config.suitable_threshold}.
          </p>
        </Notice>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Factor</TableHead>
              <TableHead>Rating 1 means</TableHead>
              <TableHead>Rating 5 means</TableHead>
              <TableHead className="text-right">Default</TableHead>
              <TableHead className="w-32">Weight (0–5)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {config.factors.map((f) => (
              <TableRow key={f.id}>
                <TableCell className="font-medium">{f.name}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{f.low}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{f.high}</TableCell>
                <TableCell className="text-right tabular-nums">{f.default_weight}</TableCell>
                <TableCell>
                  <Input type="number" min={0} max={5} step={0.1} value={weights[f.id] ?? ""} disabled={!canManage}
                    aria-label={`Weight for ${f.name}`} className="h-8 w-24"
                    onChange={(e) => setWeights((w) => ({ ...w, [f.id]: e.target.value }))} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        <div>
          <p className="mb-1 text-sm font-medium">Model affinity matrix</p>
          <p className="mb-3 text-xs text-muted-foreground">
            +2 / +1: the model is favoured when the factor is high · −1 / −2: favoured when the factor is low · 0: irrelevant.
            Edit in <code>backend/app/agents/sdlc_selection.py</code>.
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Factor</TableHead>
                {config.models.map((m) => <TableHead key={m} className="text-center text-xs">{m}</TableHead>)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {config.factors.map((f) => (
                <TableRow key={f.id}>
                  <TableCell className="whitespace-nowrap text-xs font-medium">{f.name}</TableCell>
                  {config.models.map((m) => {
                    const v = config.affinity[m]?.[f.id] ?? 0;
                    return (
                      <TableCell key={m} className="p-1 text-center">
                        <span className={cn("inline-flex h-6 w-9 items-center justify-center rounded text-xs font-medium tabular-nums", affinityClass(v))}>
                          {v > 0 ? `+${v}` : v}
                        </span>
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
            {[2, 1, 0, -1, -2].map((v) => (
              <span key={v} className="inline-flex items-center gap-1">
                <span className={cn("inline-flex h-5 w-7 items-center justify-center rounded border text-[11px]", affinityClass(v))}>{v > 0 ? `+${v}` : v}</span>
                {v > 0 ? "prefers high" : v < 0 ? "prefers low" : "irrelevant"}
              </span>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-sm font-medium">Decision rules (penalties applied after scoring)</p>
          <ul className="space-y-2">
            {config.rules.map((r) => (
              <li key={r.id} className="rounded-md border px-3 py-2 text-sm">
                <span className="font-mono text-xs font-semibold">{r.id}</span> {r.description}
                <span className="ml-2 text-xs text-muted-foreground">
                  {Object.entries(r.penalties).map(([m, p]) => `${m} −${p}`).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}

function RetentionSection({ settings, canManage, onSaved }: { settings: Settings; canManage: boolean; onSaved: () => void }) {
  const [days, setDays] = useState(String(settings.retention_days));
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDays(String(settings.retention_days)), [settings.retention_days]);

  const save = async () => {
    try {
      await api("/api/settings", { method: "PUT", body: { retention_days: Number(days) } });
      toast.success("Retention period saved");
      onSaved();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  const apply = async () => {
    setBusy(true);
    try {
      const r = await api<{ retention_days: number; analyses_deleted: number }>("/api/settings/retention/apply", { method: "POST" });
      toast.success(`Retention applied: ${r.analyses_deleted} analysis/analyses older than ${r.retention_days} days deleted`);
      setOpen(false);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const valid = Number(days) >= 7 && Number(days) <= 3650;

  return (
    <Section title="Data retention" description="Analyses (with their requirements, findings and approvals) older than this period can be purged. Audit logs are kept.">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="space-y-1.5">
          <Label htmlFor="retention">Retention period (days, 7–3650)</Label>
          <Input id="retention" type="number" min={7} max={3650} value={days} disabled={!canManage} className="w-40"
            onChange={(e) => setDays(e.target.value)} />
        </div>
        <Button variant="outline" onClick={save} disabled={!canManage || !valid}><Save className="size-4" /> Save</Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button variant="destructive" disabled={!canManage}><Trash2 className="size-4" /> Apply retention now</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Permanently delete old analyses?</DialogTitle>
              <DialogDescription>
                Every analysis started more than {settings.retention_days} days ago will be permanently deleted together with its
                requirements, findings, mappings, risks, workflow and approval history. This cannot be undone. The action is audit-logged.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="destructive" onClick={apply} disabled={busy}>{busy ? "Deleting…" : "Delete permanently"}</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      {!canManage && <p className="mt-3 text-xs text-muted-foreground">Only administrators can change retention.</p>}
    </Section>
  );
}

function UsersSection() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<ManagedUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ email: "", name: "", role: "business_analyst", password: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ManagedUser[]>("/api/users").then(setUsers).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  const update = async (u: ManagedUser, body: Partial<Pick<ManagedUser, "role" | "is_active">>) => {
    try {
      await api(`/api/users/${u.id}`, { method: "PATCH", body });
      toast.success(`Updated ${u.email}`);
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api("/api/users", { body: form });
      toast.success(`Created ${form.email}`);
      setForm({ email: "", name: "", role: "business_analyst", password: "" });
      load();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Users & roles" description="Administrator-only user management. Passwords are stored as salted PBKDF2 hashes.">
      {error ? <ErrorState message={error} retry={load} /> : !users ? <LoadingState /> : (
        <div className="space-y-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <p className="font-medium">{u.name}</p>
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                  </TableCell>
                  <TableCell>
                    <Select value={u.role} onValueChange={(role) => update(u, { role })} disabled={u.id === me?.id}>
                      <SelectTrigger className="h-8 w-48" aria-label={`Role of ${u.email}`}><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {Object.entries(ROLE_LABELS).map(([key, text]) => <SelectItem key={key} value={key}>{text}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell><StatusPill status={u.is_active ? "approved" : "rejected"} text={u.is_active ? "Active" : "Disabled"} /></TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDate(u.created_at)}</TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" disabled={u.id === me?.id} onClick={() => update(u, { is_active: !u.is_active })}>
                      {u.is_active ? "Deactivate" : "Activate"}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <form onSubmit={create} className="grid gap-3 rounded-lg border bg-muted/20 p-4 sm:grid-cols-2 lg:grid-cols-5 lg:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="new-email">Email</Label>
              <Input id="new-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-name">Name</Label>
              <Input id="new-name" required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Role</Label>
              <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(ROLE_LABELS).map(([key, text]) => <SelectItem key={key} value={key}>{text}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password">Password (≥ 10 characters)</Label>
              <Input id="new-password" type="password" required minLength={10} autoComplete="new-password" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
            <Button type="submit" disabled={busy || form.password.length < 10}><UserPlus className="size-4" /> Create user</Button>
          </form>
        </div>
      )}
    </Section>
  );
}

export default function SettingsPage() {
  const { can } = useAuth();
  const canManage = can("settings:manage");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [config, setConfig] = useState<SdlcConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([api<Settings>("/api/settings"), api<SdlcConfig>("/api/sdlc/config")])
      .then(([s, c]) => { setSettings(s); setConfig(c); })
      .catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(load, [load]);

  return (
    <>
      <PageHeader title="Settings" description="AI configuration, explainable SDLC scoring parameters, data retention and access control." />
      {error ? <ErrorState message={error} retry={load} /> : !settings || !config ? <LoadingState /> : (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            <Section title={<span className="flex items-center gap-2"><Bot className="size-4" /> AI configuration</span>}
              description="Set in backend/.env and read only by the backend - the API key is never sent to the browser.">
              <dl className="divide-y text-sm">
                {[
                  ["Mode", settings.mode === "openai" ? <StatusPill key="m" status="approved" text="OpenAI (live)" /> : <StatusPill key="m" status="warn" text="Demonstration (rule-based)" />],
                  ["OPENAI_API_KEY", settings.openai_api_key_configured ? "Configured (hidden)" : "Not configured"],
                  ["OPENAI_MODEL", <code key="model">{settings.openai_model}</code>],
                  ["OPENAI_EMBEDDING_MODEL", <code key="emb">{settings.embedding_model}</code>],
                ].map(([k, v]) => (
                  <div key={k as string} className="flex items-center justify-between gap-4 py-2">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="text-right font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
              {settings.mode !== "openai" && (
                <p className="mt-3 text-xs text-muted-foreground">
                  To enable real AI analysis, set OPENAI_API_KEY (and optionally OPENAI_MODEL) in backend/.env and restart the backend.
                  Demonstration results are always labelled as rule-based.
                </p>
              )}
            </Section>
            <Section title={<span className="flex items-center gap-2"><Database className="size-4" /> Platform</span>}>
              <dl className="divide-y text-sm">
                {[
                  ["Database", settings.database === "postgresql" ? "PostgreSQL + pgvector" : "SQLite (development)"],
                  ["Max upload size", `${settings.max_upload_mb} MB`],
                  ["Session lifetime", `${settings.session_hours} hours (HttpOnly cookie)`],
                  ["Retention period", `${settings.retention_days} days`],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-4 py-2">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="text-right font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
            </Section>
          </div>

          <WeightsSection config={config} canManage={canManage} onSaved={load} />
          <RetentionSection settings={settings} canManage={canManage} onSaved={load} />
          {can("users:manage") && <UsersSection />}

          <Section title="Role permissions" description="Read-only summary; enforced by the backend on every request.">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Role</TableHead>
                  <TableHead>Permissions (in addition to viewing results and downloading reports)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {ROLE_PERMISSIONS.map(([role, perms]) => (
                  <TableRow key={role}>
                    <TableCell className="whitespace-nowrap font-medium">{ROLE_LABELS[role]}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {perms.map((p) => <Badge key={p} variant="secondary" className="font-mono text-[11px] font-normal">{p}</Badge>)}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="mt-4 border-t pt-4">
              <p className="mb-2 text-sm font-medium">Two-stage analysis permissions</p>
              <ul className="space-y-1.5 text-sm">
                {TWO_STAGE_PERMISSIONS.map(([perm, text]) => (
                  <li key={perm} className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-3">
                    <Badge variant="secondary" className="w-fit font-mono text-[11px] font-normal">{perm}</Badge>
                    <span className="text-muted-foreground">{text}</span>
                  </li>
                ))}
              </ul>
            </div>
          </Section>
        </div>
      )}
    </>
  );
}
