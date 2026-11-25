import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "ATTa", description: "ATTa AI platform" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
