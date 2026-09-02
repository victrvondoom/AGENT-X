import { Metadata } from "next";
import { DeploymentGatePage } from "@/components/deployment-gate/DeploymentGatePage";

export const metadata: Metadata = {
  title: "SENTINEL — Deployment Gate",
  description: "Human approval & PR merge decision point",
};

export default function DeploymentGateRoute() {
  return <DeploymentGatePage />;
}
