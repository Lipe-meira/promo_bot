"use client";

import { UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import type { WorkspaceSummary } from "@/lib/api/types";

import { MeBoundary } from "./me-boundary";
import { LoadingView } from "./states";

const WorkspaceContext = createContext<WorkspaceSummary | null>(null);

export function useWorkspaceSummary(): WorkspaceSummary {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspaceSummary must be used inside <WorkspaceShell>");
  return value;
}

const NAVIGATION = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/settings", label: "Configurações" },
] as const;

function Shell({ workspace, children }: { workspace: WorkspaceSummary; children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>Promo Bot</span>
        </div>
        <nav aria-label="Principal">
          <ul>
            {NAVIGATION.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={pathname === item.href ? "page" : undefined}
                  className="nav-link"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sidebar-footer">
          <span className="muted" title={workspace.name}>
            {workspace.name}
          </span>
          <UserButton />
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}

/** Authenticated layout: admitted + has a workspace, otherwise the onboarding takes over. */
export function WorkspaceShell({ children }: { children: ReactNode }) {
  return (
    <MeBoundary>
      {(me) => <WithWorkspace workspaces={me.workspaces}>{children}</WithWorkspace>}
    </MeBoundary>
  );
}

function WithWorkspace({
  workspaces,
  children,
}: {
  workspaces: WorkspaceSummary[];
  children: ReactNode;
}) {
  const router = useRouter();
  const first = workspaces[0];
  useEffect(() => {
    if (!first) router.replace("/onboarding");
  }, [first, router]);

  if (!first) return <LoadingView label="Preparando seu workspace…" />;
  return (
    <WorkspaceContext.Provider value={first}>
      <Shell workspace={first}>{children}</Shell>
    </WorkspaceContext.Provider>
  );
}
