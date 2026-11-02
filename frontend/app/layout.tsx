import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Barlow, Barlow_Semi_Condensed } from "next/font/google";
import { MobileNav, Sidebar } from "@/components/Sidebar";
import { SlipProvider } from "@/components/SlipContext";
import "./globals.css";

const sans = Barlow({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });
const cond = Barlow_Semi_Condensed({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-cond" });

export const metadata: Metadata = {
  title: "EdgeBoard",
  description: "Football match analysis, market probabilities and value, with the reasoning shown.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${cond.variable}`}>
      <body className="font-sans">
        <SlipProvider>
        <div className="flex min-h-screen">
          <aside className="sticky top-0 hidden h-screen w-56 shrink-0 border-r border-line bg-panel md:block">
            <Sidebar modelVersion={process.env.MODEL_VERSION ?? "FootballModel_v0.3.1"} />
          </aside>
          <main className="min-w-0 flex-1"><MobileNav />{children}</main>
        </div>
        </SlipProvider>
      </body>
    </html>
  );
}
