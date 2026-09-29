import type { Metadata } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";
import { AppShell } from "@/components/AppShell";
import { AssistantContextProvider } from "@/lib/assistant-context";

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
    // The sidebar script intentionally sets data-rail before hydration.
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="min-h-full flex flex-col">
        {/* Runs before the rail is parsed, so it paints at its remembered
            width. In an effect this lands after hydration and a collapsed rail
            flashes open on every full page load. */}
        <script dangerouslySetInnerHTML={{ __html:
          'try{document.documentElement.dataset.rail='
          + 'localStorage.getItem("bom.rail")==="collapsed"?"collapsed":"expanded"}'
          + 'catch(e){}' }} />
        <SessionProvider>
          <AssistantContextProvider><AppShell>{children}</AppShell></AssistantContextProvider>
        </SessionProvider>
      </body>
    </html>
  );
}
