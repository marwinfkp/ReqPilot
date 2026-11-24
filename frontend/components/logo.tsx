import { cn } from "@/lib/utils";

export function Logo({ dark = false, className }: { dark?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#2a78d6" />
        <path d="M9 22 L16 8 L23 22 L16 18 Z" fill="white" />
        <circle cx="16" cy="24.5" r="1.6" fill="white" opacity="0.8" />
      </svg>
      <span className={cn("text-lg font-semibold tracking-tight", dark ? "text-foreground" : "text-white")}>
        Req<span className={dark ? "text-primary" : "text-[#6da7ec]"}>Pilot</span>
      </span>
    </div>
  );
}
