import { Metadata } from "next";
import { AlertsPage } from "@/components/alerts/AlertsPage";

export const metadata: Metadata = {
  title: "SENTINEL — Watchdog Alerts",
  description: "Blocked tool calls, guardrail interventions, and failed investigations",
};

export default function AlertsRoute() {
  return <AlertsPage />;
}
