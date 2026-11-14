import { ClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/ui/themes";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Promo Bot", template: "%s · Promo Bot" },
  description: "Painel do Promo Bot.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { colorScheme: "dark", themeColor: "#0b0a12" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <ClerkProvider appearance={{ theme: dark, variables: { colorPrimary: "#8b5cf6" } }}>
          {children}
        </ClerkProvider>
      </body>
    </html>
  );
}
