import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Keep the development badge from covering the sidebar's account control.
  devIndicators: false,
  // Next.js 16 blocks cross-origin access to dev resources (/_next/*) by
  // default. Without this, opening the app on 127.0.0.1 while the dev server's
  // origin is localhost silently blocks the client bundle -- pages render their
  // server HTML but never hydrate, so no data ever loads and no error appears.
  // Dev-only setting; has no effect on a production build.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
