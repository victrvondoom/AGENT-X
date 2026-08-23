import { Config, Handlers } from 'motia';
import { z } from 'zod';

export const config: Config = {
  // @ts-expect-error: Logic requires name but type definition is missing it
  name: 'ingest-alert',
  description: 'Endpoint to receive external alerts (e.g., from Prometheus or AlertManager).',
  type: 'api',
  path: '/ingest-alert',
  method: 'POST',
  emits: ['incident.detected'],
  flows: ['opsguard-flow']
};

const inputSchema = z.object({
  serverName: z.string(),
  errorLogs: z.string(),
  severity: z.string().optional(),
  timestamp: z.string().optional(),
  geminiApiKey: z.string().optional(),
  discordWebhookUrl: z.string().optional(),
});

export const handler: Handlers['ingest-alert'] = async (req: any, context: any) => {
  const { emit, logger } = context;

  const parseResult = inputSchema.safeParse(req.body);
  if (!parseResult.success) {
    return {
      status: 400,
      body: { error: 'Invalid Input', details: parseResult.error.format() }
    };
  }

  const { serverName, errorLogs, severity, geminiApiKey, discordWebhookUrl } = parseResult.data;

  logger.info(`🚨 Alert Received! Server: ${serverName}`);
  logger.info(`📜 Logs:\n${errorLogs}`);

  const incidentId = `INC-${Date.now()}`;

  await emit({
    topic: 'incident.detected',
    data: {
      incidentId,
      issueType: 'CRITICAL_OUTAGE',
      serverName,
      errorLogs,
      severity: severity || 'UNKNOWN',
      timestamp: new Date().toISOString(),
      geminiApiKey,
      discordWebhookUrl,
    } as any
  });

  return {
    status: 200,
    body: {
      success: true,
      incidentId,
      message: 'Alert received and incident workflow started.',
    }
  };
};