import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "SEC Financial Dashboard",
  description: "Local-first SEC financial dashboard",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
