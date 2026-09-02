import { Metadata } from "next";
import { CommandCenter } from "@/components/command-center/CommandCenter";

export const metadata: Metadata = {
  title: "SENTINEL — Command Center",
  description: "Real-time autonomous security investigation dashboard",
};

export default function CommandCenterPage() {
  return <CommandCenter />;
}
