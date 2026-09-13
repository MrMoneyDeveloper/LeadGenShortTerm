function sheet_() {
  const id = config_().sheet;
  if (!id) throw new Error('CONFIG_SPREADSHEET_ID');
  return SpreadsheetApp.openById(id);
}

function writeRows_(name, rows, fields) {
  if (/^VALIDATED_/.test(name) || name === 'EXPORT_INDEX') throw new Error('DELIVERY_APPEND_ONLY_TAB');
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
    const p = config_().props;
    const status = ['BACKUP', 'DASHBOARD', 'PROCESSOR', 'DELIVERY'].map(k => ({metric: k + '_status', value: p.getProperty('STATUS_' + k) || 'never_run'}));
    writeRows_('SUMMARY', Object.keys(summary).map(k => ({metric: k, value: summary[k]})).concat(status), ['metric', 'value']);
    writeRows_('SOURCE_STATS', sources);
    writeRows_('PROCESSING', queue.recent_jobs);
    writeRows_('FAILED', failures);
    if (!sheet_().getSheetByName('EXPORT_HISTORY')) writeRows_('EXPORT_HISTORY', [], ['date', 'status', 'manifest', 'parts', 'at']);
    ensureDeliveryIndex_();
    status_('DASHBOARD', 'SUCCESS', 'Final leads are appended to VALIDATED_001 and subsequent shards.');
  } catch (e) {
    status_('DASHBOARD', 'FAILED', errorCode_(e));
    try { writeRows_('FAILED', [{component: 'dashboard', code: errorCode_(e), at: new Date().toISOString()}]); } catch (ignored) {}
    throw new Error(errorCode_(e));
  } finally { lock.releaseLock(); }
}

function ensureDeliveryIndex_() {
  const book = sheet_();
  const tab = book.getSheetByName('EXPORT_INDEX') || book.insertSheet('EXPORT_INDEX');
  const fields = ['batch_id', 'status', 'rows', 'first_tab', 'first_row', 'last_tab', 'last_row', 'checksum', 'drive_file', 'at'];
  ensureGrid_(tab, 1, fields.length);
  const header = tab.getRange(1, 1, 1, fields.length).getDisplayValues()[0];
  if (header.every(v => v === '')) writeLiteralRows_(tab.getRange(1, 1, 1, fields.length), [fields]);
  else if (!sameStrings_(header, fields)) throw new Error('DELIVERY_INDEX_CONFLICT');
  tab.setFrozenRows(1);
  return tab;
}

function deliveryIndex_(batch, status, url) {
  const tab = ensureDeliveryIndex_(), count = tab.getLastRow();
  const ids = count > 1 ? tab.getRange(2, 1, count - 1, 1).getDisplayValues() : [];
  const existing = ids.findIndex(row => row[0] === String(batch.batch_id));
  const first = batch.items[0], last = batch.items[batch.items.length - 1];
  const row = [batch.batch_id, status, batch.items.length, first.tab, first.row, last.tab, last.row,
    batch.checksum, url, new Date().toISOString()].map(String);
  const destination = existing >= 0 ? existing + 2 : count + 1;
  ensureGrid_(tab, destination, row.length);
  writeLiteralRows_(tab.getRange(destination, 1, 1, row.length), [row]);
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
