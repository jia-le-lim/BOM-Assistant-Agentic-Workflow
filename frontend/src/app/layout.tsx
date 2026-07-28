import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import { Nav } from "@/components/Nav";

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
          <Nav />
          <main className="max-w-[1400px] mx-auto w-full px-5 py-6">{children}</main>
        </SessionProvider>
      </body>
    </html>
  );
}
