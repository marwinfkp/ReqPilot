"use client";

import { Award, CheckCircle2, Layers, MinusCircle, PlusCircle, Scale } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth";
import { useLoadedAnalysis } from "@/components/analysis-context";
import { HorizontalBars } from "@/components/charts";
import { BulletList, EmptyState, GeneratedBy, Notice, Section, StatusPill } from "@/components/common";
import { CitationItem } from "@/components/evidence";
import { ReviewDialog } from "@/components/review-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

function Rating({ value }: { value: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${value} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => <span key={i} className={cn("size-2 rounded-full", i <= value ? "bg-[var(--series-1)]" : "bg-muted")} />)}
      <span className="ml-1.5 text-xs tabular-nums">{value}/5</span>
    </span>
  );
}

function Affinity({ value }: { value: number }) {
  const text = value > 0 ? `+${value}` : String(value);
  return <span className={cn("inline-block w-8 rounded text-center text-xs font-medium tabular-nums",
    value > 0 ? "bg-blue-50 text-blue-800" : value < 0 ? "bg-orange-50 text-orange-800" : "text-muted-foreground")}>{text}</span>;
}

export default function SdlcPage() {
  const { analysis: a, reload } = useLoadedAnalysis();
  const { can } = useAuth();
  const sdlc = a.sdlc;
  const [model, setModel] = useState(sdlc?.recommended_model ?? "");
  const [override, setOverride] = useState("");
  if (!sdlc) return <EmptyState title="No SDLC recommendation" description="The SDLC Selection Agent did not complete; see the agent log on the overview page." />;

  const top = sdlc.ranking[0];
  const inspected = sdlc.ranking.find((r) => r.model === model) ?? top;
  const decide = async (action: string, comment: string, chosen?: string) => {
    await api(`/api/analyses/${a.id}/sdlc/review`, { body: { action, comment, model: chosen } });
    await reload();
  };

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden border-primary/30 py-0">
        <CardContent className="grid gap-6 bg-gradient-to-br from-primary/10 via-primary/5 to-transparent p-6 md:grid-cols-[1fr_auto] md:p-8">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-primary"><Award className="size-4" /> Recommended SDLC model
              {sdlc.is_hybrid && <Badge className="bg-[var(--series-1)]">Hybrid</Badge>}
              <StatusPill status={sdlc.status} /><GeneratedBy value={sdlc.generated_by} />
            </div>
            <h2 className="text-3xl font-semibold tracking-tight md:text-4xl">{sdlc.recommended_model}</h2>
            <p className="max-w-2xl text-sm text-muted-foreground">{top.description}</p>
            {sdlc.status !== "pending_approval" && (
              <p className="text-sm"><CheckCircle2 className="mr-1 inline size-4 text-emerald-600" />
                Adopted model (human decision): <strong>{sdlc.approved_model}</strong></p>
            )}
            <div className="flex flex-wrap gap-2 pt-1">
              <ReviewDialog title="Approve SDLC recommendation" confirmLabel="Approve" disabled={!can("sdlc:approve")}
                description={`Adopt ${sdlc.recommended_model} for ${a.project.name}. This decision is recorded with your name and role.`}
                trigger={<Button disabled={!can("sdlc:approve")}>Approve recommendation</Button>}
                onSubmit={(c) => decide("approve", c)} />
              <ReviewDialog title="Override SDLC recommendation" confirmLabel="Override" commentRequired disabled={!can("sdlc:approve") || !override}
                description="Choose a different model and justify the decision. The workflow is regenerated for the chosen model and needs approval again."
                trigger={<Button variant="outline" disabled={!can("sdlc:approve")}>Override…</Button>}
                onSubmit={(c) => decide("override", c, override)}>
                <div className="space-y-2">
                  <Label>Model to adopt</Label>
                  <Select value={override} onValueChange={setOverride}>
                    <SelectTrigger className="w-full"><SelectValue placeholder="Select a model" /></SelectTrigger>
                    <SelectContent>{sdlc.ranking.map((r) => <SelectItem key={r.model} value={r.model}>{r.model} ({r.score})</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              </ReviewDialog>
              {!can("sdlc:approve") && <span className="self-center text-xs text-muted-foreground">Approval requires the Project Manager role.</span>}
            </div>
          </div>
          <div className="flex flex-col items-center justify-center rounded-xl border bg-background px-8 py-5 text-center">
            <span className="text-5xl font-semibold tabular-nums tracking-tight">{top.score}</span>
            <span className="text-xs text-muted-foreground">suitability score / 100</span>
            <span className={cn("mt-2 text-xs font-medium", top.suitable ? "text-emerald-700" : "text-amber-800")}>{top.suitable ? "Above suitability threshold (60)" : "Below threshold - low confidence"}</span>
          </div>
        </CardContent>
      </Card>

      <Notice>
        Suitability scores are <strong>not probabilities</strong>. Each score is the weighted percentage of a model&apos;s preferences that this
        project&apos;s 13 characteristics satisfy: <code className="text-xs">score = 100 × Σ(weight × |affinity| × fit) / Σ(weight × |affinity|)</code>,
        minus explicit rule penalties. The calculation is deterministic; the AI never sets the scores.
      </Notice>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Ranked SDLC models" description="All seven candidate models, scored with the same formula.">
          <HorizontalBars data={sdlc.ranking.map((r) => ({ name: r.model, value: r.score }))} max={100} highlight={sdlc.recommended_model} />
        </Section>
        <Section title="Why this model fits" description="Deterministic explanation from the score breakdown, plus the AI narrative in OpenAI mode.">
          <p className="whitespace-pre-line text-sm leading-relaxed">{sdlc.justification}</p>
          {sdlc.hybrid_explanation && (
            <div className="mt-4 rounded-lg border border-primary/30 bg-primary/5 p-3">
              <p className="flex items-center gap-2 text-sm font-medium"><Layers className="size-4" /> Hybrid composition</p>
              <BulletList items={top.components} />
            </div>
          )}
          {(sdlc.citations?.length ?? 0) > 0 && (
            <div className="mt-4">
              <p className="text-sm font-medium">Supporting guidance from the knowledge base</p>
              <p className="mb-2 text-xs text-muted-foreground">Supporting evidence only - retrieved after the ranking was fixed; it never changes a factor or a score.</p>
              <ul className="space-y-2">{sdlc.citations!.map((c, i) => <CitationItem key={`${c.chunk_id}-${i}`} c={c} />)}</ul>
            </div>
          )}
        </Section>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Section title={<span className="flex items-center gap-2"><PlusCircle className="size-4 text-emerald-600" /> Advantages</span>}><BulletList items={top.advantages} /></Section>
        <Section title={<span className="flex items-center gap-2"><MinusCircle className="size-4 text-red-600" /> Disadvantages</span>}><BulletList items={top.disadvantages} /></Section>
        <Section title={<span className="flex items-center gap-2"><Scale className="size-4" /> Risks and trade-offs</span>}><BulletList items={sdlc.trade_offs} /></Section>
      </div>

      <Section title="Project decision factors" description="Rated 1 (low) to 5 (high) from the analysed requirements. In OpenAI mode the model may adjust a rule rating by at most ±1, with a reason.">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>Factor</TableHead><TableHead>Rating</TableHead><TableHead>Rule rating</TableHead><TableHead>Evidence / explanation</TableHead><TableHead>AI adjustment</TableHead></TableRow></TableHeader>
            <TableBody>
              {sdlc.factors.map((f) => (
                <TableRow key={f.id}>
                  <TableCell className="whitespace-nowrap font-medium">{f.name}<p className="text-[11px] font-normal text-muted-foreground">1 = {f.low_meaning} · 5 = {f.high_meaning}</p></TableCell>
                  <TableCell><Rating value={f.value} /></TableCell>
                  <TableCell className="tabular-nums">{f.rule_value}</TableCell>
                  <TableCell className="min-w-[280px] whitespace-normal text-sm">{f.explanation}{f.assumption && <Badge variant="outline" className="ml-2 font-normal">assumption</Badge>}</TableCell>
                  <TableCell className="min-w-[200px] whitespace-normal text-xs">{f.adjustment ? <><Badge variant="secondary">{f.adjustment > 0 ? "+1" : "−1"}</Badge> {f.llm_reason}</> : <span className="text-muted-foreground">–</span>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section title="Factor-wise scoring breakdown"
        description="How each factor contributes to a model's score. Affinity +2 = the model is favoured when the factor is high; −2 = favoured when it is low."
        actions={
          <Select value={inspected.model} onValueChange={setModel}>
            <SelectTrigger className="w-[260px]"><SelectValue /></SelectTrigger>
            <SelectContent>{sdlc.ranking.map((r) => <SelectItem key={r.model} value={r.model}>{r.rank}. {r.model}</SelectItem>)}</SelectContent>
          </Select>
        }>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader><TableRow><TableHead>Factor</TableHead><TableHead className="text-center">Rating</TableHead><TableHead className="text-center">Affinity</TableHead><TableHead className="text-center">Weight</TableHead><TableHead>Fit</TableHead><TableHead className="text-right">Points</TableHead></TableRow></TableHeader>
            <TableBody>
              {inspected.breakdown.map((b) => (
                <TableRow key={b.factor}>
                  <TableCell className="whitespace-nowrap">{b.name}</TableCell>
                  <TableCell className="text-center tabular-nums">{b.value}</TableCell>
                  <TableCell className="text-center"><Affinity value={b.affinity} /></TableCell>
                  <TableCell className="text-center tabular-nums">{b.weight}</TableCell>
                  <TableCell className="w-48"><div className="h-1.5 rounded-full bg-muted"><div className="h-1.5 rounded-full bg-[var(--series-1)]" style={{ width: `${b.fit * 100}%` }} /></div></TableCell>
                  <TableCell className="text-right tabular-nums">{b.points.toFixed(2)} / {b.max_points.toFixed(2)}</TableCell>
                </TableRow>
              ))}
              <TableRow className="font-medium">
                <TableCell colSpan={5}>Base score = 100 × {inspected.breakdown.reduce((s, b) => s + b.points, 0).toFixed(2)} / {inspected.breakdown.reduce((s, b) => s + b.max_points, 0).toFixed(2)}</TableCell>
                <TableCell className="text-right tabular-nums">{inspected.base_score}</TableCell>
              </TableRow>
              {inspected.penalties.map((p) => (
                <TableRow key={p.rule}><TableCell colSpan={5} className="text-sm text-muted-foreground">Rule {p.rule}: {p.description}</TableCell><TableCell className="text-right tabular-nums text-red-700">{p.points}</TableCell></TableRow>
              ))}
              <TableRow className="font-semibold"><TableCell colSpan={5}>Final suitability score</TableCell><TableCell className="text-right tabular-nums">{inspected.score}</TableCell></TableRow>
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section title="Ranked alternatives">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {sdlc.ranking.slice(1).map((r) => (
            <div key={r.model} className="rounded-lg border p-4">
              <div className="flex items-start justify-between gap-2"><p className="font-medium">{r.rank}. {r.model}</p><span className="text-lg font-semibold tabular-nums">{r.score}</span></div>
              <p className="mt-1 text-xs text-muted-foreground">{r.description}</p>
              {r.strengths.length > 0 && <p className="mt-2 text-xs"><span className="font-medium text-emerald-700">Fits: </span>{r.strengths.join(", ")}</p>}
              {r.weaknesses.length > 0 && <p className="mt-1 text-xs"><span className="font-medium text-red-700">Mismatches: </span>{r.weaknesses.join(", ")}</p>}
              {r.penalties.map((p) => <p key={p.rule} className="mt-1 text-xs text-muted-foreground">Rule {p.rule}: {p.points} points</p>)}
              {r.llm_note && <p className="mt-2 border-t pt-2 text-xs">{r.llm_note}</p>}
            </div>
          ))}
        </div>
        <div className="mt-4">
          <p className="mb-1 text-sm font-medium">Decision rules applied</p>
          <BulletList items={sdlc.rules_applied.map((r) => `${r.rule}: ${r.description} (${Object.entries(r.penalties).map(([m, p]) => `${m} −${p}`).join(", ")})`)} empty="No decision rules fired for this project." />
        </div>
      </Section>
    </div>
  );
}
