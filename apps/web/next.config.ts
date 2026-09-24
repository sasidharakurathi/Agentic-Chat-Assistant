import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The shared package ships TypeScript source, not a build: Next compiles it.
  transpilePackages: ["@assistant-studio/shared"],
};

export default nextConfig;
