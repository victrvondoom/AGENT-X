import { Handlers } from 'motia';

// --- Configuration ---
// This step wakes up when 'ticket.escalation' happens.
export const config = {
    name: 'create-ticket',
    description: 'Creates a ticket in Linear/Jira if the incident is escalated.',
    type: 'event',
    subscribes: ['ticket.escalation'],
    emits: [], // Ends the flow (conceptually)
    flows: ['opsguard-flow']
};

// --- Handler Logic ---
export const handler: Handlers['create-ticket'] = async (data: any, context: any) => {
    const { logger, state } = context;
    const { incidentId, reason, approver } = data;

    logger.info(`🎫 CREATING TICKET for Incident ${incidentId}...`);
    logger.warn(`Reason: ${reason}`);

    // Artificial Delay to simulate API call to Linear/Jira
    await new Promise(r => setTimeout(r, 1000));

    // Simulation: Create a mock ticket ID
    const ticketId = `LIN-${Math.floor(Math.random() * 10000)}`;
    const ticketUrl = `https://linear.app/opsguard/issue/${ticketId}`;

    logger.info(`✅ TICKET CREATED: ${ticketId}`);
    logger.info(`🔗 Link: ${ticketUrl}`);

    // Update State (Escalated)
    const incident = (await state.get('active_incidents', incidentId)) || {};
    await state.set('active_incidents', incidentId, {
        ...incident,
        status: 'escalated',
        ticketId,
        ticketUrl,
        escalatedBy: approver,
        escalatedAt: new Date().toISOString()
    });

    logger.info(`🔄 Incident ${incidentId} status updated to 'escalated'.`);
};
