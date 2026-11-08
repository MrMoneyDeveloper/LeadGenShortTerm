/**
 * Operator web endpoint for headless setup and bounded runs (no arbitrary code execution).
 *
 * Authorization: the request body carries a secret whose SHA-256 must equal OPS_SECRET_SHA256.
 * The secret itself lives only in the operator's ignored .env (APPS_SCRIPT_API_TOKEN).
 * The endpoint stops answering after OPS_EXPIRES_AT; delete the web deployment when done.
 * Responses never include property values, tokens or lead data.
 */
const OPS_SECRET_SHA256 = '7ff92fb612bf4034f488521d064eeaa4138ac86a9cf9aa18fe1731544e52a41b';
const OPS_EXPIRES_AT = '2026-12-05T00:00:00Z';
const OPS_PROPERTIES = ['RENDER_BASE_URL', 'DASHBOARD_API_TOKEN', 'PROCESSOR_TRIGGER_TOKEN', 'GOOGLE_SPREADSHEET_ID',
  'GOOGLE_DRIVE_BACKUP_FOLDER_ID', 'DELIVERY_ENABLED', 'BACKUPS_ENABLED', 'DASHBOARD_ENABLED',
  'PROCESSOR_SCHEDULE_ENABLED', 'DELIVERY_EXPIRES_AT', 'SAMPLES_ENABLED'];
const OPS_FLAGS = ['DELIVERY_ENABLED', 'BACKUPS_ENABLED', 'DASHBOARD_ENABLED', 'PROCESSOR_SCHEDULE_ENABLED',
  'SAMPLES_ENABLED'];

function opsOut_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

function opsHex_(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(value), Utilities.Charset.UTF_8)
    .map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join('');
}

function opsStatus_(expect) {
  const props = PropertiesService.getScriptProperties();
  const properties = {};
  OPS_PROPERTIES.forEach(k => {
    const value = props.getProperty(k);
    const entry = {set: value !== null && value !== ''};
    if (OPS_FLAGS.indexOf(k) >= 0) entry.value = value;
    if (expect && expect[k]) entry.matches = value !== null && opsHex_(value) === expect[k];
    properties[k] = entry;
  });
  const triggers = ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction());
  let tabs = [];
  try {
    tabs = sheet_().getSheets().map(s => ({name: s.getName(), rows: s.getLastRow()}));
  } catch (e) { tabs = [{error: errorCode_(e)}]; }
  const statuses = {};
  ['DELIVERY', 'DASHBOARD', 'BACKUP', 'PROCESSOR'].forEach(k => {
    try { statuses[k] = JSON.parse(props.getProperty('STATUS_' + k) || 'null'); } catch (e) { statuses[k] = null; }
  });
  return {status: 'ok', properties: properties, triggers: triggers, tabs: tabs, statuses: statuses,
    delivery_state_pending: props.getProperty('DELIVERY_STATE') !== null};
}

function writeSamples_() {
  const data = json_('/dashboard/samples?limit=1000');
  const rows = (data.items || []).map(r => ({
    source: r.source, outcome: r.outcome, priority: r.priority, published_at: r.published_at,
    source_url: r.source_url, profile_url: (r.profile || {}).profile_url || '', username: (r.profile || {}).username || '',
    text: String(r.text || '').slice(0, 2000), detail: JSON.stringify(r.detail || {}), captured_at: r.captured_at,
  }));
  writeRows_('SAMPLES', rows, ['source', 'outcome', 'priority', 'published_at', 'source_url', 'profile_url',
    'username', 'text', 'detail', 'captured_at']);
  return rows.length;
}

/** Validate the newest delivered rows. Returns checks and non-personal fields only. */
function peekLeads_(count) {
  const book = sheet_();
  const tabs = book.getSheets().filter(t => VALIDATED_TAB_.test(t.getName()));
  const totals = tabs.map(t => ({tab: t.getName(), data_rows: Math.max(0, t.getLastRow() - 1)}));
  const products = ['MOTOR', 'HOME', 'CONTENTS', 'LIFE', 'FUNERAL', 'DISABILITY', 'OTHER', 'UNKNOWN'];
  const rows = [];
  tabs.forEach(tab => {
    const last = tab.getLastRow();
    if (last < 2) return;
    const width = tab.getLastColumn();
    const header = tab.getRange(1, 1, 1, width).getDisplayValues()[0];
    const start = Math.max(2, last - count + 1);
    tab.getRange(start, 1, last - start + 1, width).getDisplayValues().forEach(values => {
      const v = {};
      header.forEach((name, i) => { v[name] = values[i]; });
      let host = '';
      try { host = /^https:\/\/([^/]+)/.exec(v.source_url || '')[1]; } catch (e) { host = ''; }
      rows.push({tab: tab.getName(), created_at: v.created_at, source: v.source, product_type: v.product_type,
        score: v.score, rank_score: v.rank_score, validation_status: v.validation_status, salutation: v.salutation,
        source_host: host,
        checks: {
          lead_id: /^[A-Za-z0-9_-]+:\d+$/.test(v.lead_id || ''),
          source_url_https: /^https:\/\//.test(v.source_url || ''),
          evidence_present: (v.source_evidence || '').length > 0,
          product_known: products.indexOf(v.product_type) >= 0,
          salutation_ok: /^Good day( [^,]+)?,$/.test(v.salutation || ''),
          email_or_flag: (v.email || '') !== '' || v.validation_status === 'NO_EMAIL',
        }});
    });
  });
  rows.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return {totals: totals, newest: rows.slice(0, count)};
}

function doPost(e) {
  let request;
  try { request = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) {
    return opsOut_({status: 'bad_request'});
  }
  if (!(Date.now() < Date.parse(OPS_EXPIRES_AT))) return opsOut_({status: 'expired'});
  if (typeof request.secret !== 'string' || request.secret.length < 32 || opsHex_(request.secret) !== OPS_SECRET_SHA256) {
    return opsOut_({status: 'unauthorized'});
  }
  try {
    const props = PropertiesService.getScriptProperties();
    switch (request.action) {
      case 'status':
        return opsOut_(opsStatus_(request.expect));
      case 'configure': {
        const set = request.set || {};
        const remove = request.remove || [];
        const keys = Object.keys(set).concat(remove);
        if (keys.some(k => OPS_PROPERTIES.indexOf(k) < 0)) return opsOut_({status: 'property_not_allowed'});
        Object.keys(set).forEach(k => props.setProperty(k, String(set[k])));
        remove.forEach(k => props.deleteProperty(k));
        return opsOut_({status: 'ok', updated: Object.keys(set), removed: remove});
      }
      case 'install_triggers': {
        const wanted = request.triggers || [];
        if (wanted.indexOf('delivery') >= 0) installDeliveryTrigger();
        if (wanted.indexOf('reporting') >= 0) installReportingTriggers();
        if (wanted.indexOf('processor') >= 0) installProcessorTrigger();
        return opsOut_({status: 'ok', triggers: ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction())});
      }
      case 'remove_triggers':
        removeLeadgenTriggers();
        return opsOut_({status: 'ok', triggers: ScriptApp.getProjectTriggers().map(t => t.getHandlerFunction())});
      case 'deliver':
        deliverFinalLeads();
        return opsOut_({status: 'ok', delivery: JSON.parse(props.getProperty('STATUS_DELIVERY') || 'null')});
      case 'dashboard':
        refreshDashboard();
        return opsOut_({status: 'ok', dashboard: JSON.parse(props.getProperty('STATUS_DASHBOARD') || 'null')});
      case 'backup':
        dailyBackup();
        return opsOut_({status: 'ok', backup: JSON.parse(props.getProperty('STATUS_BACKUP') || 'null')});
      case 'samples':
        return opsOut_({status: 'ok', rows: writeSamples_()});
      case 'snapshot': {
        // Current destination snapshot for database recovery: no values, only counts.
        const tabs = {};
        sheet_().getSheets().filter(t => VALIDATED_TAB_.test(t.getName()))
          .forEach(t => { tabs[t.getName()] = Math.max(0, t.getLastRow() - 1); });
        return opsOut_({status: 'ok', snapshot: {spreadsheet_sha256: opsHex_(config_().sheet),
          captured_at: new Date().toISOString(), tabs: tabs}});
      }
      case 'peek':
        return opsOut_({status: 'ok', peek: peekLeads_(Math.min(Number(request.rows) || 5, 20))});
      default:
        return opsOut_({status: 'unknown_action'});
    }
  } catch (err) {
    return opsOut_({status: 'error', code: errorCode_(err)});
  }
}
