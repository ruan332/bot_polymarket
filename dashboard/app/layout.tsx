import type { Metadata } from "next";
import type { ReactNode } from "react";
import { IBM_Plex_Mono, JetBrains_Mono } from "next/font/google";

import "./globals.css";

const display = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-display",
});

const mono = IBM_Plex_Mono({
  weight: ["400", "500", "600"],
  subsets: ["latin"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "Polymarket System Console",
  description: "Advanced multi-agent terminal for Polymarket operations.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className={`${display.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
