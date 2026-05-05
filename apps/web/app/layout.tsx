import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CyberSure AI Questionnaire Engine",
  description: "AI-powered cyber security questionnaire automation."
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
