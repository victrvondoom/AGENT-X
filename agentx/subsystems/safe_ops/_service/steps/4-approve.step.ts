import { Config, Handlers } from 'motia';

// --- Configuration ---
export const config: Config = {
  // @ts-expect-error: Logic requires name but type definition is missing it
  name: 'approve-fix',
  description: 'API Endpoint to handle approval/escalation actions from the Discord notification.',
  type: 'api',
  path: '/approve-fix',
  method: 'GET', 
  emits: ['approval.received', 'ticket.escalation'],
  flows: ['opsguard-flow'],
  virtualSubscribes: ['manual.intervention']
};

// --- Handler Logic ---
export const handler: Handlers['approve-fix'] = async (req: any, context: any) => {
  const { emit, logger, state } = context;

  logger.info(`🔍 DEBUG: Request received at /approve-fix`);
  logger.info(`🔍 DEBUG: req argument type: ${typeof req}`);

  // Safe logging of req structure
  try {
    logger.info(`🔍 DEBUG: req keys: ${Object.keys(req).join(', ')}`);
  } catch (e) {
    logger.info(`🔍 DEBUG: Could not log req keys`);
  }

  // Robust Parsing Strategy
  // 1. Try Standard Express/Motia Query & Body & queryParams (as seen in logs)
  let incidentId = req?.queryParams?.incidentId || req?.query?.incidentId || req?.body?.incidentId;
  let action = req?.queryParams?.action || req?.query?.action || req?.body?.action;

  // 2. Try 'incidentId' directly on the object (sometimes payload IS the data)
  if (!incidentId && req?.incidentId) {
    incidentId = req.incidentId;
  }

  // 3. Fallback: Parse URL manually from req.url
  if (!incidentId && req?.url) {
    try {
      // req.url might be relative (e.g. '/approve-fix?params...')
      // We provide a dummy base to satisfy the URL constructor
      const urlObj = new URL(req.url, 'http://localhost');
      incidentId = urlObj.searchParams.get('incidentId');
      action = urlObj.searchParams.get('action');
      logger.info(`🔍 DEBUG: Manually parsed from req.url -> incidentId: ${incidentId}`);
    } catch (e) {
      logger.warn(`Failed to parse req.url: ${req.url}`);
    }
  }

  // 4. Fallback: Check context.req (if the handler argument is just the body)
  if (!incidentId && context?.req) {
    incidentId = context.req.query?.incidentId || context.req.body?.incidentId;
    action = context.req.query?.action || context.req.body?.action;
    logger.info(`🔍 DEBUG: Found incidentId in context.req`);
  }

  // --- VALIDATION ---
  if (!incidentId) {
    logger.error('❌ Failed to extract incidentId from request.');
    logger.error(`❌ Debug Dump: req=${JSON.stringify(req)}`);
    return { status: 400, body: { error: 'Missing incidentId' } };
  }

  // --- ESCALATION FLOW ---
  if (action === 'escalate') {
    logger.warn(`⚠️ ESCALATION RECEIVED for Incident ${incidentId}`);

    await emit({
      topic: 'ticket.escalation',
      data: { incidentId, reason: "Manual Escalation by Admin", approver: "AdminUser" } as any
    });

    return {
      status: 200,
      body: { message: `🚫 Incident ${incidentId} ESCALATED. Ticket is being created.` }
    };
  }

  // --- APPROVAL FLOW (Default) ---
  logger.info(`👍 APPROVAL RECEIVED for Incident ${incidentId} via Webhook.`);

  // Retrieve analysis to get the command
  const incidentData = (await state.get('active_incidents', incidentId)) || {};
  const fixCommand = incidentData.aiAnalysis?.commandToRun || "echo 'No command found'";

  await emit({
    topic: 'approval.received',
    data: { incidentId, fixCommand } as any
  });

  return {
    status: 200,
    body: { message: `✅ Fix authorized for ${incidentId}. Executing now...` }
  };
};