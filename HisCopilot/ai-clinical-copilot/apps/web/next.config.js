/** @type {import('next').NextConfig} */
// All browser calls are same-origin `/api/v1/*` → rewritten to the FastAPI backend. We deliberately do NOT use the
// Next rewrite proxy for long jobs: the API answers immediately (202 + job id) and the browser polls, so the
// default 30 s proxy timeout (which killed 49 s ASR calls in the old build) can no longer be hit.
const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8011";
module.exports = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.NEXT_DIST_DIR || ".next",          // lets a second instance (e2e) run beside the main one without sharing build output
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },
  experimental: { proxyTimeout: 120000 },
};
