import type { NextConfig } from "next";

/** Sent with every page (task 6.3). The sign-in tokens live in the
 *  browser's storage, so the page must not be framed by another site
 *  (clickjacking an Approve button), must not guess a response's type, and
 *  must not send its address (which names assistants and conversations) to
 *  sites it links to. A script policy needs per-request nonces in Next and
 *  is not set here. */
const SECURITY_HEADERS = [
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'",
  },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The shared package ships TypeScript source, not a build: Next compiles it.
  transpilePackages: ["@assistant-studio/shared"],
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
