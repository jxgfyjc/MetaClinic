#!/usr/bin/env node
/**
 * HTTPS reverse proxy for the doctor-web frontend + backend API.
 *
 * Why: mobile browsers only expose navigator.mediaDevices.getUserMedia
 * in a "secure context" (HTTPS or localhost). When the app is accessed
 * via HTTP (e.g. through an HTTP tunnel), navigator.mediaDevices is
 * undefined and VoiceRecorder shows "当前环境不支持麦克风".
 *
 * This proxy listens on HTTPS (port 3443) with a self-signed cert.
 * - /api/* and /mock-his/*  → forwarded directly to backend (port 8001)
 * - everything else         → forwarded to Next.js dev server (port 3000)
 *
 * Tunnel to 3443 instead of 3000, access https://<host>:3443 on mobile,
 * and accept the self-signed cert warning.
 *
 * Usage: node https-proxy.js
 */

const https = require("https");
const http = require("http");
const fs = require("fs");
const path = require("path");

const HTTPS_PORT = 3443;
const NEXTJS = { host: "127.0.0.1", port: 3000 };
const BACKEND = { host: "127.0.0.1", port: 8001 };

const certDir = path.join(__dirname, "cert");
const keyPath = path.join(certDir, "localhost-key.pem");
const certPath = path.join(certDir, "localhost-cert.pem");

if (!fs.existsSync(keyPath) || !fs.existsSync(certPath)) {
  console.error("TLS cert not found at", certDir);
  process.exit(1);
}

const sslOptions = {
  key: fs.readFileSync(keyPath),
  cert: fs.readFileSync(certPath),
};

function pickUpstream(url) {
  if (url.startsWith("/api/") || url.startsWith("/mock-his/")) {
    return BACKEND;
  }
  return NEXTJS;
}

const server = https.createServer(sslOptions, (req, res) => {
  const up = pickUpstream(req.url);
  const proxyReq = http.request(
    {
      host: up.host,
      port: up.port,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: `${up.host}:${up.port}` },
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode, proxyRes.headers);
      proxyRes.pipe(res, { end: true });
    }
  );
  proxyReq.on("error", (e) => {
    if (!res.headersSent) res.writeHead(502);
    res.end(`Upstream error: ${e.message}`);
  });
  req.pipe(proxyReq, { end: true });
});

// WebSocket upgrade (Next.js HMR / hot reload)
server.on("upgrade", (req, socket) => {
  // HMR/websocket always goes to Next.js
  const up = NEXTJS;
  const proxyReq = http.request({
    host: up.host,
    port: up.port,
    path: req.url,
    method: "GET",
    headers: { ...req.headers, host: `${up.host}:${up.port}` },
  });
  proxyReq.on("upgrade", (proxyRes) => {
    socket.write(
      `HTTP/1.1 101 Switching Protocols\r\n` +
      Object.entries(proxyRes.headers).map(([k, v]) => `${k}: ${v}`).join("\r\n") +
      "\r\n\r\n"
    );
    proxyRes.pipe(socket);
    socket.pipe(proxyRes);
  });
  proxyReq.on("error", () => socket.destroy());
  proxyReq.end();
});

server.listen(HTTPS_PORT, "0.0.0.0", () => {
  console.log(`HTTPS proxy on https://0.0.0.0:${HTTPS_PORT}`);
  console.log(`  /api/* /mock-his/* → http://${BACKEND.host}:${BACKEND.port}`);
  console.log(`  other            → http://${NEXTJS.host}:${NEXTJS.port}`);
  console.log(`  Mobile: access https://<server-ip>:${HTTPS_PORT} and accept the cert warning`);
});
