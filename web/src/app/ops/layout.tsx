import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ops — flight_scans",
  robots: { index: false, follow: false },
};

export default function OpsLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
