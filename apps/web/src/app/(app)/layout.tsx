import type { ReactNode } from "react";

import { SessionGate } from "@/lib/session";

export default function AppLayout({ children }: { children: ReactNode }) {
  return <SessionGate>{children}</SessionGate>;
}
