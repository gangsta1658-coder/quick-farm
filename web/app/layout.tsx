import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Quick Farm 控制台",
  description: "AWS Quick 注册机控制台",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
