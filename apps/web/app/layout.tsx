import type { Metadata } from "next";
import { Fira_Code, Fira_Sans, Fira_Sans_Condensed } from "next/font/google";
import type { ReactNode } from "react";

import { ThemeProvider } from "@/components/theme-provider";
import { DialogProvider } from "@/components/ui/dialog";
import { ToastProvider } from "@/components/ui/toast";
import { AuthProvider } from "@/lib/auth";
import { cn } from "@/lib/utils";

import "./globals.css";
import "@xyflow/react/dist/style.css";

/* Fira in three cuts (docs/DESIGN.md section 3): Sans for all UI and
   reading, Sans Condensed for names and titles, Code for code only. */
const firaSans = Fira_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-fira-sans",
});
const firaCondensed = Fira_Sans_Condensed({
  subsets: ["latin"],
  weight: ["500", "600"],
  variable: "--font-fira-condensed",
});
const firaCode = Fira_Code({ subsets: ["latin"], variable: "--font-fira-code" });

export const metadata: Metadata = {
  title: "Assistant Studio",
  description:
    "Build an assistant by wiring together what it can use: your documents, databases, tools and MCP servers. Then chat with it and see every step it took.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
          firaSans.variable,
          firaCondensed.variable,
          firaCode.variable,
          "min-h-screen antialiased",
        )}
      >
        <ThemeProvider>
          <ToastProvider>
            <DialogProvider>
              <AuthProvider>{children}</AuthProvider>
            </DialogProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
