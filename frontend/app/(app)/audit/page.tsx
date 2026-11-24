"use client";

import { ChevronLeft, ChevronRight, ScrollText, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth";
import { EmptyState, ErrorState, LoadingState, PageHeader, Section } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { ROLE_LABELS, formatDate } from "@/lib/format";

type AuditEntry = {
  id: number;
  timestamp: string;
  user_email: string;
  user_role: string;
  action: string;
  entity_type: string;
  entity_id: string;
  details: Record<string, unknown>;
  ip_address: string;
};

const PAGE_SIZE = 50;

function AuditTable() {
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [filters, setFilters] = useState({ action: "", user: "" });
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; items: AuditEntry[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    const query = new URLSearchParams({ action: filters.action, user_email: filters.user, limit: String(PAGE_SIZE), offset: String(offset) });
    api<{ total: number; items: AuditEntry[] }>(`/api/audit-logs?${query}`).then(setData).catch((e) => setError(errorMessage(e)));
  }, [filters, offset]);
  useEffect(load, [load]);

  const apply = (event: React.FormEvent) => {
    event.preventDefault();
    setOffset(0);
    setFilters({ action: action.trim(), user: user.trim() });
  };

  return (
    <Section
      title={data ? `${data.total.toLocaleString()} event(s)` : "Events"}
      description="Logins, analyses, approvals, knowledge-base changes, exports, settings and user administration."
    >
      <form onSubmit={apply} className="mb-4 flex flex-col gap-2 sm:flex-row">
        <Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="Action contains… (e.g. approval, login)" />
        <Input value={user} onChange={(e) => setUser(e.target.value)} placeholder="User e-mail contains…" />
        <Button type="submit"><Search className="size-4" /> Filter</Button>
      </form>
      {error ? <ErrorState message={error} retry={load} /> : !data ? <LoadingState /> : data.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">No audit events match these filters.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>User</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Entity</TableHead>
                <TableHead>Details</TableHead>
                <TableHead>IP</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="whitespace-nowrap text-xs">{formatDate(e.timestamp)}</TableCell>
                  <TableCell className="text-xs">
                    <p className="font-medium">{e.user_email}</p>
                    <p className="text-muted-foreground">{ROLE_LABELS[e.user_role] ?? e.user_role}</p>
                  </TableCell>
                  <TableCell><Badge variant="secondary" className="font-mono text-[11px] font-normal">{e.action}</Badge></TableCell>
                  <TableCell className="whitespace-nowrap text-xs">{e.entity_type ? `${e.entity_type} ${e.entity_id}` : "–"}</TableCell>
                  <TableCell className="max-w-[420px]">
                    <code className="block truncate text-[11px] text-muted-foreground" title={JSON.stringify(e.details, null, 2)}>
                      {Object.keys(e.details ?? {}).length ? JSON.stringify(e.details) : "–"}
                    </code>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{e.ip_address || "–"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="mt-4 flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                <ChevronLeft className="size-4" /> Previous
              </Button>
              <Button variant="outline" size="sm" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </Section>
  );
}

export default function AuditPage() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Audit Logs" description="Tamper-evident accountability trail of every significant action taken in ReqPilot, by people and by the analysis pipeline." />
      {can("audit:view") ? <AuditTable /> : (
        <EmptyState icon={ScrollText} title="Restricted to auditors and administrators"
          description="Your role does not include the audit:view permission. Ask an administrator if you need access." />
      )}
    </>
  );
}
