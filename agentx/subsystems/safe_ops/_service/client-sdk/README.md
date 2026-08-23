# OpsGuard Client SDK 🛡️

> **The Autonomous AI SRE for your production services.**
> Automatically detect, analyze, and coordinate production incidents.

[![npm version](https://img.shields.io/npm/v/opsguard.svg)](https://www.npmjs.com/package/opsguard)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/Status-Beta-orange)]()

**OpsGuard** acts as an intelligent sidecar for your application. When your app crashes or throws an exception, OpsGuard sends it to an **AI Agent** that:
1.  **Analyzes** the root cause using **Gemini 2.5 Flash** (optimized for speed/cost).
2.  **Suggests** a precise remediation plan (risk assessed).
3.  **Simulates** execution steps (SSH, Health checks) for validation.
4.  **Generates** a physical audit log (`OPSGUARD_AUDIT_LOG.txt`) as proof of work.

---

## ⚡ Quick Start

Get your application protected in less than 30 seconds.

### 1. Install
```bash
npm install opsguard
```

### 2. Initialize
Run our interactive setup wizard to connect your project to the OpsGuard Cloud(Live on Railway).
```bash
npx opsguard-init
```
*This command creates a `.env` file with your secure credentials.*

### 3. Integrate
Add OpsGuard to your application's error handling logic.

```javascript
import { OpsGuard } from 'opsguard';

// Initialize the agent (configuration is auto-loaded from environment)
const agent = new OpsGuard();

async function main() {
  try {
    await performCriticalTask();
  } catch (error) {
    // 🚀 Capture Exception - OpsGuard takes over from here
    agent.captureException(error);
  }
}
```

---

## 🔧 Environment Configuration

The CLI tool usually handles this for you, but you can also configure your application manually using standard environment variables.

| Variable | Description | Required |
|----------|-------------|----------|
| `OPSGUARD_SERVICE_NAME` | A unique identifier for your service (e.g. `payment-api-prod`). | **Yes** |
| `GEMINI_API_KEY` | Your Google Gemini API Key for autonomous analysis. | Optional |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook for real-time incident alerts. | Optional |

> **Note:** The SDK comes pre-configured to communicate with the OpsGuard Production Cloud(Live on Railway). No endpoint configuration is needed.

---

## 🧩 Framework Integration

### Express.js
```javascript
app.use((err, req, res, next) => {
  opsguard.captureException(err);
  res.status(500).send("Internal Server Error");
});
```

### Next.js / React
```javascript
// Global Error Boundary or Component
componentDidCatch(error, errorInfo) {
  opsguard.captureException(error);
}
```

---

## 🛡️ Security & Privacy
*   **Logs**: Error logs are sent securely to the OpsGuard backend for analysis.
*   **Keys**: Your API keys (Gemini/Discord) are stored only in your deployment environment variables and are never hardcoded in the package.

---

## 🤝 Community & Support
*   Found a bug? [Open an Issue](https://github.com/shubham-01-star/opsguard-ai-sre/issues)
*   Want to contribute? Check out our [Contribution Guide](CONTRIBUTING.md).

---
Built with ❤️ by the **OpsGuard Team**.
