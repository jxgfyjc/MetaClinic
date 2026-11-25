import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "MetaClinic",
  description: "中西医结合门诊临床副驾 · 私有化部署",
  formatDetection: { telephone: false, date: false, email: false, address: false },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#2656c4" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
