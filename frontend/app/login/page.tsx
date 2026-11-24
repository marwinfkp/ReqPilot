"use client";

import { ArrowRight, Bot, GitBranch, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage } from "@/lib/api";

const DEMO_ACCOUNTS = [
  ["analyst@reqpilot.local", "Business Analyst"],
  ["pm@reqpilot.local", "Project Manager"],
  ["compliance@reqpilot.local", "Compliance Officer"],
  ["security@reqpilot.local", "Security Officer"],
  ["auditor@reqpilot.local", "Auditor"],
  ["admin@reqpilot.local", "Administrator"],
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("analyst@reqpilot.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/login", { body: { email, password } });
      router.replace("/dashboard");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between bg-sidebar p-12 text-sidebar-foreground lg:flex">
        <Logo />
        <div className="space-y-8">
          <h1 className="max-w-md text-4xl font-semibold leading-tight tracking-tight text-white">
            From financial requirements to the right development lifecycle.
          </h1>
          <ul className="space-y-4 text-sm">
            {[
              [Bot, "Targeted clarification questions, then twelve AI agents analyse your full requirement set"],
              [ShieldCheck, "Evidence-linked compliance, security and risk analysis with human approval"],
              [GitBranch, "Explainable, deterministic SDLC ranking and a project-specific workflow"],
            ].map(([Icon, text], i) => {
              const I = Icon as typeof Bot;
              return (
                <li key={i} className="flex items-start gap-3">
                  <span className="rounded-md bg-sidebar-accent p-1.5"><I className="size-4 text-sidebar-primary" /></span>
                  <span className="pt-1">{text as string}</span>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="text-xs text-sidebar-foreground/50">Academic prototype · synthetic demonstration data only</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="mb-2 lg:hidden"><Logo dark /></div>
            <CardTitle className="text-xl">Sign in to ReqPilot</CardTitle>
            <CardDescription>Use one of the seeded demo accounts. The password is set by DEMO_USER_PASSWORD in backend/.env.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
              </div>
              {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Signing in…" : <>Sign in <ArrowRight className="size-4" /></>}
              </Button>
            </form>
            <div className="mt-6">
              <p className="mb-2 text-xs font-medium text-muted-foreground">Demo accounts (one per role)</p>
              <div className="grid grid-cols-2 gap-2">
                {DEMO_ACCOUNTS.map(([account, role]) => (
                  <button key={account} type="button" onClick={() => setEmail(account)}
                    className={`rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted ${email === account ? "border-primary bg-primary/5" : ""}`}>
                    <span className="block font-medium">{role}</span>
                    <span className="text-muted-foreground">{account}</span>
                  </button>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
