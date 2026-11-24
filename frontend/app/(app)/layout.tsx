import { AppShell } from "@/components/app-shell";
import { AuthProvider } from "@/components/auth";

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  );
}
