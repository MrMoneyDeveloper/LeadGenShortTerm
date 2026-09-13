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

function backupTick() {
  if (config_().props.getProperty('BACKUPS_ENABLED') !== 'true') return;
  dailyBackup();
}

function dashboardTick() {
  if (config_().props.getProperty('DASHBOARD_ENABLED') !== 'true') return;
  refreshDashboard();
}

/** Explicit Phase-2-or-later setup only. No triggers are installed automatically. */
function installReportingTriggers() {
  const names = ['dashboardTick', 'backupTick'];
  ScriptApp.getProjectTriggers().filter(t => names.indexOf(t.getHandlerFunction()) >= 0)
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('dashboardTick').timeBased().everyMinutes(15).create();
  ScriptApp.newTrigger('backupTick').timeBased().everyHours(1).create();
}

function installProcessorTrigger() {
  if (config_().props.getProperty('PROCESSOR_SCHEDULE_ENABLED') !== 'true') throw new Error('CONFIG_PROCESSOR_SCHEDULE_DISABLED');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'processorTick')
    .forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('processorTick').timeBased().everyMinutes(1).create();
}

function removeLeadgenTriggers() {
  const names = ['processorTick', 'dashboardTick', 'backupTick'];
  ScriptApp.getProjectTriggers().filter(t => names.indexOf(t.getHandlerFunction()) >= 0)
    .forEach(t => ScriptApp.deleteTrigger(t));
}
