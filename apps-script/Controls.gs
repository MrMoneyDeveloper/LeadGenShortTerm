function pausePipeline() {
  api_('/admin/pipeline/pause', 'post', {}, true);
  status_('PROCESSOR', 'PAUSED', 'In-flight work may finish.');
}

function resumePipeline() {
  api_('/admin/pipeline/resume', 'post', {}, true);
  status_('PROCESSOR', 'RESUMED', 'Environment, YAML and source flags still apply.');
}

function processorTick() {
  if (config_().props.getProperty('PROCESSOR_SCHEDULE_ENABLED') !== 'true') return;
  try {
    api_('/jobs/tick', 'post', {}, true);
    status_('PROCESSOR', 'TICK_REQUESTED', 'Inspect PROCESSING for job completion.');
  } catch (e) {
    status_('PROCESSOR', 'FAILED', errorCode_(e));
    throw new Error(errorCode_(e));
  }
}

function deliveryTick() {
  const props = config_().props;
  if (props.getProperty('DELIVERY_ENABLED') !== 'true') return;
  const expires = props.getProperty('DELIVERY_EXPIRES_AT');
  if (expires) {
    const deadline = Date.parse(expires);
    if (!Number.isFinite(deadline)) throw new Error('CONFIG_DELIVERY_EXPIRATION');
    if (Date.now() >= deadline) {
      props.setProperty('DELIVERY_ENABLED', 'false');
      ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'deliveryTick')
        .forEach(t => ScriptApp.deleteTrigger(t));
      status_('DELIVERY', 'EXPIRED', 'Bounded delivery window ended.');
      return;
    }
  }
  try {
    deliverFinalLeads();
    if (expires) {
      const status = JSON.parse(props.getProperty('STATUS_DELIVERY') || '{}');
      const evidence = JSON.parse(props.getProperty('DELIVERY_TEST_EVENTS') || '[]');
      evidence.push({at: new Date().toISOString(), status: status.status || 'UNKNOWN'});
      props.setProperty('DELIVERY_TEST_EVENTS', JSON.stringify(evidence.slice(-10)));
    }
  } catch (e) {
    status_('DELIVERY', 'FAILED', errorCode_(e));
    throw new Error(errorCode_(e));
  }
}

function backupTick() {
  if (config_().props.getProperty('BACKUPS_ENABLED') !== 'true') return;
  dailyBackup();
}

function dashboardTick() {
  if (config_().props.getProperty('DASHBOARD_ENABLED') !== 'true') return;
  refreshDashboard();
}

/** Explicit setup only. No triggers are installed automatically. */
function installReportingTriggers() {
  const names = ['dashboardTick', 'backupTick'];
  ScriptApp.getProjectTriggers().filter(t => names.indexOf(t.getHandlerFunction()) >= 0)
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('dashboardTick').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('backupTick').timeBased().everyHours(1).create();
}

/** Final-lead delivery is independent of acquisition scheduling. */
function installDeliveryTrigger() {
  if (config_().props.getProperty('DELIVERY_ENABLED') !== 'true') throw new Error('CONFIG_DELIVERY_DISABLED');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'deliveryTick')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('deliveryTick').timeBased().everyMinutes(5).create();
}

function installProcessorTrigger() {
  if (config_().props.getProperty('PROCESSOR_SCHEDULE_ENABLED') !== 'true') throw new Error('CONFIG_PROCESSOR_SCHEDULE_DISABLED');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'processorTick')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('processorTick').timeBased().everyMinutes(1).create();
}

function removeLeadgenTriggers() {
  const names = ['processorTick', 'deliveryTick', 'dashboardTick', 'backupTick'];
  ScriptApp.getProjectTriggers().filter(t => names.indexOf(t.getHandlerFunction()) >= 0)
    .forEach(t => ScriptApp.deleteTrigger(t));
}
