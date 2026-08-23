import type { CronConfig, Handlers } from 'motia';

export const config: CronConfig = {
    name: 'hourly-health-check',
    description: 'Scheduled job to proactively scan for vulnerabilities.',
    type: 'cron',
    // Runs every 2 minutes for demo purposes (formerly every hour)
    cron: '0 * * * *',
    emits: ['incident.detected'],
    flows: ['opsguard-flow']
};

export const handler: Handlers['hourly-health-check'] = async (context: any) => {
    const { emit, logger, state } = context;

    logger.info('🛡️ STARTING HOURLY SECURITY SCAN...');

    // --- Prevent Loop: Check for Active Incidents ---
    // We fetch all active incidents and check if any are NOT resolved.
    const activeIncidents = await state.get('active_incidents') || {};
    const hasActiveIncident = Object.values(activeIncidents).some((inc: any) =>
        inc.status && inc.status !== 'resolved'
    );

    if (hasActiveIncident) {
        logger.warn('⚠️ Skipping Health Check: An incident is already active and unresolved.');
        return;
    }

    logger.info('🔍 Checking dependencies against CVE Database...');

    // Simulate Scan Delay
    await new Promise(r => setTimeout(r, 1500));

    // --- Simulation Logic ---
    // In a real app, we would run `npm audit --json` here.
    // For Demo: We randomly decide to find a vulnerability or not, 
    // OR we can force it if a specific generic env var is set, but for now randomly (low chance) or just force it for the 'Cron Demo'.
    // Let's force it for the purpose of the PRD "Proactive Security" demo if we run it manually.

    const vulnerabilityFound = true;

    if (vulnerabilityFound) {
        logger.error('❌ CRITICAL VULNERABILITY DETECTED: CVE-2025-9988');
        logger.warn('📦 Package: next@14.1.0');
        logger.warn('⚠️ Risk: Remote Code Execution (RCE)');

        const incidentId = `SEC-${Date.now()}`;

        // Initialize State for this new incident
        await state.set('active_incidents', incidentId, {
            id: incidentId,
            status: 'detected',
            detectedAt: new Date().toISOString()
        });

        // Trigger the Main Loop
        await emit({
            topic: 'incident.detected',
            data: {
                incidentId,
                issueType: 'SECURITY_VULNERABILITY',
                serverName: 'Production-Web-01',
                errorLogs: 'Audit Report: Critical RCE in next@14.1.0. Recommendation: Upgrade to 14.2.0 immediately.',
                severity: 'CRITICAL',
                timestamp: new Date().toISOString(),
            } as any
        });

        logger.info(`🚨 Incident ${incidentId} created. Handing over to AI Analyst.`);
    } else {
        logger.info('✅ System Secure. No vulnerabilities found.');
    }
};
