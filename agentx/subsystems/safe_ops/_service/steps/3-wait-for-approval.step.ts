import { Config, Handlers } from 'motia';
import * as dotenv from 'dotenv';
dotenv.config();

// --- Configuration ---
// This step runs when AI needs approval.
export const config: Config = {
  // @ts-expect-error: Logic requires name but type definition is missing it
  name: 'notify-human',
  description: 'Pauses the workflow and sends a notification to Discord requesting human approval.',
  type: 'event',
  subscribes: ['human.approval.needed'],
  emits: [],
  flows: ['opsguard-flow'],
  virtualEmits: [{ topic: 'manual.intervention', label: 'Waiting for Admin' }]
};

// --- Handler Logic ---
export const handler: Handlers['notify-human'] = async (data: any, context: any) => {
  const { logger, state } = context;
  const { incidentId, analysis } = data;

  // 1. Format the message for the human
  const message = `🚨 OpsGuard Alert: ${incidentId}\nIssue: ${analysis.rootCause}\nFix: ${analysis.commandToRun}`;

  // 2. Update State to 'waiting_for_approval'
  // This tells the dashboard to show a "Approve" button.
  await state.set('active_incidents', incidentId, {
    ...await state.get('active_incidents', incidentId),
    status: 'waiting_approval'
  });

  // 3. Send Notification to Discord
  const webhookUrl = data.discordWebhookUrl || process.env.DISCORD_WEBHOOK_URL;

  if (!webhookUrl) {
    logger.error('❌ Missing DISCORD_WEBHOOK_URL in environment variables.');
    return;
  }

  try {
    // Native fetch is available in Node.js v18+
    await fetch(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        embeds: [{
          title: `🚨 OpsGuard Alert: ${incidentId}`,
          description: `**Issue Detected:**\n${analysis.rootCause}\n\n[✅ **CLICK TO APPROVE FIX**](http://localhost:3000/approve-fix?incidentId=${incidentId})\n[⚠️ **CLICK TO ESCALATE**](http://localhost:3000/approve-fix?incidentId=${incidentId}&action=escalate)`,
          color: 15548997, // RED
          fields: [
            { name: 'Recommended Fix', value: `\`${analysis.commandToRun}\``, inline: false },
            { name: 'Risk Level', value: analysis.riskLevel, inline: true },
            { name: 'Confidence', value: `${analysis.confidence}%`, inline: true }
          ],
          footer: { text: 'Links work locally. Ensure backend is running.' }
        }]
      })
    });
    logger.info('📱 Notification sent to Discord successfully.');
  } catch (err) {
    logger.error('Failed to send Discord notification', err);
  }

  logger.info(`⏳ WORKFLOW PAUSED: Waiting for admin approval for ${incidentId}...`);
};