"use client";

import {
  BarChart3, CheckCircle2, Compass, FilePlus2, FileDown, FlaskConical, FolderKanban, LayoutDashboard, Library,
  ListChecks, LogOut, Menu, Network, ScrollText, Settings, ShieldAlert, ShieldCheck, Workflow,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

const NAV = [
  {
    group: "Workspace",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/projects", label: "Projects", icon: FolderKanban },
      { href: "/analyse", label: "Analyse Requirements", icon: FilePlus2 },
    ],
  },
  {
    group: "Latest analysis",
    items: [
      { href: "/analyses/latest", label: "Analysis Results", icon: BarChart3 },
      { href: "/analyses/latest/requirements", label: "Requirements Explorer", icon: ListChecks },
      { href: "/analyses/latest/compliance", label: "Compliance Centre", icon: ShieldCheck },
      { href: "/analyses/latest/security", label: "Security & Risk", icon: ShieldAlert },
      { href: "/analyses/latest/sdlc", label: "SDLC Recommendation", icon: Compass },
      { href: "/analyses/latest/workflow", label: "SDLC Workflow", icon: Workflow },
      { href: "/analyses/latest/traceability", label: "Traceability Matrix", icon: Network },
    ],
  },
  {
    group: "Governance",
    items: [
      { href: "/approvals", label: "Approval Centre", icon: CheckCircle2 },
      { href: "/knowledge", label: "Knowledge Base", icon: Library },
      { href: "/reports", label: "Reports & Exports", icon: FileDown },
      { href: "/audit", label: "Audit Logs", icon: ScrollText },
      { href: "/evaluation", label: "Evaluation", icon: FlaskConical },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

function isActive(pathname: string, href: string) {
  if (href === "/analyses/latest") return /^\/analyses\/[^/]+$/.test(pathname);
  if (href.startsWith("/analyses/latest/")) {
    const section = href.split("/").pop();
    return new RegExp(`^/analyses/[^/]+/${section}`).test(pathname);
  }
  return pathname === href || pathname.startsWith(href + "/");
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
      {NAV.map((section) => (
        <div key={section.group}>
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-sidebar-foreground/50">
            {section.group}
          </p>
          <ul className="space-y-0.5">
            {section.items.map(({ href, label, icon: Icon }) => (
              <li key={href}>
                <Link
                  href={href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                    isActive(pathname, href)
                      ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                      : "text-sidebar-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  {label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function ModeBadge() {
  const [health, setHealth] = useState<{ mode: string; model: string | null } | null>(null);
  useEffect(() => {
    api<{ mode: string; model: string | null }>("/api/health").then(setHealth).catch(() => setHealth(null));
  }, []);
  if (!health) return null;
  const live = health.mode === "openai";
  return (
    <span
      title={live ? "Agents use the OpenAI API together with rule-based checks" : "No OpenAI API key configured: agents run their rule-based baseline only"}
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
        live ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900",
      )}
    >
      <span className={cn("size-2 rounded-full", live ? "bg-emerald-500" : "bg-amber-500")} />
      {live ? `OpenAI · ${health.model}` : "Demonstration mode · rule-based agents"}
    </span>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  const sidebar = (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-16 items-center border-b border-sidebar-border px-6">
        <Logo />
      </div>
      <SidebarNav onNavigate={() => setOpen(false)} />
      <div className="border-t border-sidebar-border p-4 text-xs text-sidebar-foreground/60">
        From financial requirements to the right development lifecycle.
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block">{sidebar}</aside>
      <div className="flex min-w-0 flex-1 flex-col lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur md:px-8">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
                <Menu className="size-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 border-0 p-0">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              {sidebar}
            </SheetContent>
          </Sheet>
          <ModeBadge />
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium leading-tight">{user?.name}</p>
              <p className="text-xs text-muted-foreground">{ROLE_LABELS[user?.role ?? ""] ?? user?.role}</p>
            </div>
            <Button variant="outline" size="sm" onClick={logout}>
              <LogOut className="size-4" /> Sign out
            </Button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8 md:py-8">{children}</main>
      </div>
    </div>
  );
}
