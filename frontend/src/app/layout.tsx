import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "BOM Review Assistant",
  description:
    "Decision-support console for WINGS stocking-parameter review. " +
    "The engine calculates, the engineer approves.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // Geist fonts removed: next/font/google fetches at build time and this
    // network blocks it. The palette specifies system-ui anyway.
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
