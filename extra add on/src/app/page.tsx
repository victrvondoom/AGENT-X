import { Metadata } from "next";
import { LandingPage } from "@/components/landing/LandingPage";

export const metadata: Metadata = {
  title: "SENTINEL — Evidence-driven autonomous security verification",
  description:
    "Six agents take a scanner finding, decide whether it is genuinely exploitable in your codebase, prove it in a sandbox, write the fix, re-test it, and seal the evidence.",
};

export default function Home() {
  return <LandingPage />;
}
