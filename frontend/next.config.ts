import type { NextConfig } from "next";

const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The Docker image sets NEXT_STANDALONE=1 for a self-contained server; `npm start` uses the normal build.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  // No framework branding: no dev-tools button and no X-Powered-By header.
  devIndicators: false,
  poweredByHeader: false,
  // The dashboard talks to the local backend through /api, so no CORS or keys in the browser.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
