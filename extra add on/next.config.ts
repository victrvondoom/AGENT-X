import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export for Firebase Hosting. Every route in this app is already
  // prerendered as static and fetches its data client-side from the agent
  // engine, so there is no server-side rendering to lose here.
  output: "export",
  images: { unoptimized: true },
  // Firebase Hosting serves /route/ as /route/index.html.
  trailingSlash: true,
};

export default nextConfig;
