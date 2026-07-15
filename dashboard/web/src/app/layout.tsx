import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "East Africa Flood Risk · GIK-IceChain",
  description:
    "Daily admin-1 flood-risk decision support for East Africa  ECMWF IFS ENS exceedance + ICPAC CRMA Bayesian Network.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Sora:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
