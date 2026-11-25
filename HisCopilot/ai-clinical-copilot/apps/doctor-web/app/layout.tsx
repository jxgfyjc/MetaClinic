import type { Metadata, Viewport } from "next";
import "./globals.css";
import AppShell from "@/components/AppShell";

export const metadata: Metadata = {
  title: "象元 AI 诊疗",
  description: "象元 AI 诊疗 · 中西医结合门诊临床副驾 · 私有化部署",
  // iOS 自动将电话号码/邮箱/日期/地址识别为链接，会篡改文本节点导致
  // 服务端渲染的 HTML 与客户端 hydration 后的 DOM 不一致 (hydration mismatch)。
  // 禁用自动识别，确保 SSR HTML = client DOM。
  formatDetection: {
    telephone: false,
    date: false,
    email: false,
    address: false,
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // 覆盖 formatDetection 的 meta 标签写法，确保旧版 iOS Safari 也禁用
  // Next.js Metadata API 的 formatDetection 会生成等价的 meta 标签，
  // 这里显式设 viewport 确保移动端缩放正确。
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
