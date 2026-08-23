import { Config, Handlers } from 'motia';
import * as dotenv from 'dotenv';
dotenv.config(); // Ensure env vars are loaded

// --- Configuration ---
export const config: Config = {
    // @ts-expect-error: Logic requires name but type definition is missing it
    name: 'analyze-incident',
    description: 'AI Agent analyzes the incident logs using Gemini 1.5 Flash to determine root cause and suggest a fix.',
    type: 'event',
    subscribes: ['incident.detected'],
    emits: ['human.approval.needed'],
    flows: ['opsguard-flow']
};

// --- Handler Logic ---
export const handler: Handlers['analyze-incident'] = async (data: any, context: any) => {
    const { emit, logger, state } = context;
    const { incidentId, errorLogs, geminiApiKey: customKey } = data;

    logger.info(`🤖 AI ANALYST: Thinking about Incident ${incidentId}...`);

    // --- Real AI Logic (Gemini) ---
    let aiAnalysis;
    const apiKey = customKey || process.env.GEMINI_API_KEY;

    // Debug Log (Masked)
    const maskedKey = apiKey ? `${apiKey.substring(0, 5)}...` : 'undefined';
    logger.info(`🔑 DEBUG: API Key Status: ${maskedKey}`);

    logger.info(`🔑 DEBUG: API Key Status: ${maskedKey}`);

    if (!apiKey) {
        // If no key is provided (neither in env nor in request), we cannot proceed with real AI.
        // But we DO NOT crash via env check. We just return a fallback.
        logger.warn("⚠️ No GEMINI_API_KEY provided in Env or Request. Using Simulation Mode.");
        // Fallback simulation
        aiAnalysis = {
            rootCause: "Simulated: Memory Leak in Service (Missing API Key)",
            riskLevel: "MEDIUM",
            suggestedFix: "Restart Service",
            commandToRun: "docker restart payment-gateway",
            confidence: 50
        };
    } else {
        try {
            const prompt = `
            ROLE: Experienced Site Reliability Engineer (SRE) at a Fortune 500 company.
            TASK: Diagnose the following application logs and prescribe a remediation plan.

            INPUT LOGS:
            "${errorLogs}"

            INSTRUCTIONS:
            1. Analyze the stack trace / error message deeply.
            2. Identify the specific library, service, or resource causing the issue.
            3. Determine RISK LEVEL (HIGH = Data Loss/Downtime, MEDIUM = degraded performance, LOW = minor noise).
            4. Suggest a PRECISE terminal command to fix it. Prefer 'safe' commands like restarts, rollbacks, or cache clears.

            OUTPUT FORMAT (JSON ONLY):
            {
                "rootCause": "Technical explanation (max 15 words)",
                "riskLevel": "HIGH" | "MEDIUM" | "LOW",
                "suggestedFix": "Actionable title (e.g. 'Restart Pod')",
                "commandToRun": "The exact command (e.g. 'kubectl rollout restart deployment/api-gateway')",
                "confidence": number,
                "reasoning": "Why this fix will work"
            }
            `;

            const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    contents: [{ parts: [{ text: prompt }] }]
                })
            });

            const result = await response.json();

            // Check for API Error Response
            if (result.error) {
                logger.error(`❌ Gemini API Error: ${JSON.stringify(result.error)}`);
                throw new Error(result.error.message);
            }

            const text = result.candidates?.[0]?.content?.parts?.[0]?.text;
            if (!text) {
                logger.error(`❌ Gemini Empty Response: ${JSON.stringify(result)}`);
                throw new Error("Empty response from Gemini");
            }

            // Clean up JSON markdown if present
            const jsonText = text?.replace(/```json/g, '').replace(/```/g, '').trim();
            aiAnalysis = JSON.parse(jsonText);
            logger.info("🧠 Gemini Analysis Complete");

        } catch (error) {
            logger.error("❌ Gemini API Call Failed Exception", error);
            aiAnalysis = {
                rootCause: "AI Analysis Failed (Network/API Error)",
                riskLevel: "UNKNOWN",
                suggestedFix: "Check Backup Logs",
                commandToRun: "echo 'Check Logs Manually'",
                confidence: 0,
                reasoning: "Fallback due to API failure"
            };
        }
    }

    logger.info(`💡 SOLUTION FOUND: ${aiAnalysis.suggestedFix}`);

    // Persist Analysis
    const existingData = (await state.get('active_incidents', incidentId)) || {};
    await state.set('active_incidents', incidentId, {
        ...existingData,
        status: 'waiting_approval',
        aiAnalysis: aiAnalysis
    });

    logger.info(`⏳ Awaiting Admin Approval for Incident ${incidentId}...`);

    await emit({
        topic: 'human.approval.needed',
        data: { incidentId, analysis: aiAnalysis, discordWebhookUrl: data.discordWebhookUrl } as any
    });
};