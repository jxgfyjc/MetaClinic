/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Proxy API + MockHIS requests through the Next.js server so browser-side
  // (client component) fetches are same-origin — the browser cannot reach
  // 127.0.0.1:8001 on the server host, so we proxy instead of hardcoding.
  // Server components also use these relative paths (API_BASE="") and resolve
  // through the same rewrites at request time.
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://127.0.0.1:8001";
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/mock-his/:path*", destination: `${backend}/mock-his/:path*` },
    ];
  },
};
module.exports = nextConfig;
