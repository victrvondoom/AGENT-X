#!/usr/bin/env node
import fs from 'fs';
import path from 'path';
import readline from 'readline';

const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
});

const question = (query: string): Promise<string> => {
    return new Promise((resolve) => rl.question(query, resolve));
};

async function setup() {
    console.log("\n🛡️  OpsGuard AI SRE - Client Setup 🛡️\n");
    console.log("Let's configure your project to connect with OpsGuard.\n");

    const PRODUCTION_ENDPOINT = "https://opsguard-ai-sre.up.railway.app";
    console.log(`ℹ️  Using OpsGuard Endpoint: ${PRODUCTION_ENDPOINT}`);

    const serviceName = await question("1. Enter Your Service Name (e.g., payment-api): ") || "unnamed-service";

    console.log("\n--- Optional: AI & Notifications ---");
    const geminiKey = await question("2. Enter Gemini API Key (Optional): ");
    const discordWebhook = await question("3. Enter Discord Webhook URL (Optional): ");

    const envContent = `
# --- OpsGuard Configuration ---
OPSGUARD_ENDPOINT=${PRODUCTION_ENDPOINT}
OPSGUARD_SERVICE_NAME=${serviceName}
${geminiKey ? `GEMINI_API_KEY=${geminiKey}` : '# GEMINI_API_KEY='}
${discordWebhook ? `DISCORD_WEBHOOK_URL=${discordWebhook}` : '# DISCORD_WEBHOOK_URL='}
`;

    // Detect file to write
    const envPath = path.resolve(process.cwd(), '.env');
    const exists = fs.existsSync(envPath);

    if (exists) {
        console.log(`\n⚠️  .env file already exists at ${envPath}`);
        const append = await question("Do you want to append these variables? (y/n): ");
        if (append.toLowerCase().startsWith('y')) {
            fs.appendFileSync(envPath, `\n${envContent}`);
            console.log("✅ Configuration appended to .env");
        } else {
            console.log("❌ Setup skipped.");
        }
    } else {
        fs.writeFileSync(envPath, envContent);
        console.log(`✅ Created .env file at ${envPath}`);
    }

    console.log("\n🎉 Setup Complete! You can now use OpsGuard in your code:");
    console.log(`\nimport { OpsGuard } from 'opsguard';\nconst client = new OpsGuard();\n`);

    rl.close();
}

setup();
