function sheet_() {
  const id = config_().sheet;
  if (!id) throw new Error('CONFIG_SPREADSHEET_ID');
  return SpreadsheetApp.openById(id);
}

function writeRows_(name, rows, fields) {
  const book = sheet_();
  const tab = book.getSheetByName(name) || book.insertSheet(name);
  fields = fields || Array.from(new Set(rows.reduce((a, r) => a.concat(Object.keys(r)), [])));
  if (!fields.length) fields = ['status'];
  const values = [fields].concat(rows.map(r => fields.map(f => safeCell_(r[f]))));
  // Clear only after all server data for this write was fetched successfully.
  tab.clearContents();
  if (tab.getMaxRows() < values.length) tab.insertRowsAfter(tab.getMaxRows(), values.length - tab.getMaxRows());
  if (tab.getMaxColumns() < fields.length) tab.insertColumnsAfter(tab.getMaxColumns(), fields.length - tab.getMaxColumns());
  tab.getRange(1, 1, values.length, fields.length).setValues(values);
  tab.setFrozenRows(1);
}

function refreshDashboard() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const summary = json_('/dashboard/summary');
    const sources = json_('/dashboard/source-stats');
    const queue = json_('/dashboard/queue');
    const failures = json_('/dashboard/failures');
    const leads = json_('/exports/validated?format=json&limit=500').items;
    const p = config_().props;
    const status = ['BACKUP', 'DASHBOARD', 'PROCESSOR'].map(k => ({metric: k + '_status', value: p.getProperty('STATUS_' + k) || 'never_run'}));
    writeRows_('SUMMARY', Object.keys(summary).map(k => ({metric: k, value: summary[k]})).concat(status), ['metric', 'value']);
    writeRows_('SOURCE_STATS', sources);
    writeRows_('PROCESSING', queue.recent_jobs);
    writeRows_('FAILED', failures);
    writeRows_('VALIDATED', leads, ['id', 'email', 'source', 'source_url', 'email_source_url', 'product_type', 'score', 'validation_status', 'created_at']);
    if (!sheet_().getSheetByName('EXPORT_HISTORY')) writeRows_('EXPORT_HISTORY', [], ['date', 'status', 'manifest', 'parts', 'at']);
    status_('DASHBOARD', 'SUCCESS', 'Validated tab is capped at 500 rows; full lead exports are in Drive.');
  } catch (e) {
    status_('DASHBOARD', 'FAILED', errorCode_(e));
    try { writeRows_('FAILED', [{component: 'dashboard', code: errorCode_(e), at: new Date().toISOString()}]); } catch (ignored) {}
    throw new Error(errorCode_(e));
  } finally { lock.releaseLock(); }
}

function exportHistory_(state, status, url) {
  const book = sheet_();
  let tab = book.getSheetByName('EXPORT_HISTORY');
  if (!tab) {
    writeRows_('EXPORT_HISTORY', [], ['date', 'status', 'manifest', 'parts', 'at']);
    tab = book.getSheetByName('EXPORT_HISTORY');
  }
  const n = tab.getLastRow();
  const dates = n > 1 ? tab.getRange(2, 1, n - 1, 1).getDisplayValues() : [];
  const index = dates.findIndex(r => r[0] === state.date);
  const row = [state.date, status, url || '', state.part - 1, new Date().toISOString()].map(safeCell_);
  tab.getRange(index >= 0 ? index + 2 : n + 1, 1, 1, row.length).setValues([row]);
}
