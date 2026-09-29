import type { Metadata } from "next";
import { LoginForm } from "@/components/LoginForm";
import { safeReturnPath } from "@/lib/pilot-auth";

export const metadata: Metadata = { title: "Sign in · BOM Review", robots: { index: false, follow: false } };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <LoginForm nextPath={safeReturnPath(typeof next === "string" ? next : null)} />;
}
