/** Configure Script Properties, never spreadsheet cells or checked-in source. */
function config_() {
  const p = PropertiesService.getScriptProperties();
  const base = (p.getProperty('RENDER_BASE_URL') || '').replace(/\/$/, '');
  if (!/^https:\/\/[^/?#@]+$/.test(base)) throw new Error('CONFIG_RENDER_BASE_URL');
  return {props: p, base: base, sheet: p.getProperty('GOOGLE_SPREADSHEET_ID'),
    folder: p.getProperty('GOOGLE_DRIVE_BACKUP_FOLDER_ID')};
}

function api_(path, method, body, operator, key) {
  const cfg = config_();
  const token = cfg.props.getProperty(operator ? 'PROCESSOR_TRIGGER_TOKEN' : 'DASHBOARD_API_TOKEN');
  if (!token) throw new Error('CONFIG_API_TOKEN');
  const options = {method: method || 'get', muteHttpExceptions: true, followRedirects: false,
    headers: {Authorization: 'Bearer ' + token}};
  if (body !== undefined) {
    options.contentType = 'application/json';
    options.payload = JSON.stringify(body);
  }
  if (key) options.headers['Idempotency-Key'] = key;
  for (let attempt = 0; attempt < 3; attempt++) {
    let response;
    try { response = UrlFetchApp.fetch(cfg.base + path, options); }
    catch (e) {
      if (attempt === 2) throw new Error('RENDER_NETWORK_UNAVAILABLE');
      Utilities.sleep(1000 * Math.pow(2, attempt));
      continue;
    }
    const code = response.getResponseCode();
    if (code >= 200 && code < 300) return response;
    if ((code === 429 || code >= 500) && attempt < 2) {
      Utilities.sleep(1000 * Math.pow(2, attempt));
      continue;
    }
    // Never log response bodies or tokens.
    throw new Error('RENDER_HTTP_' + code);
  }
  throw new Error('RENDER_UNAVAILABLE');
}

function json_(path) { return JSON.parse(api_(path).getContentText()); }

function safeCell_(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  const s = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return (/^\s*[=+\-@]/.test(s) || /^[\t\r\n]/.test(s)) ? "'" + s : s;
}

function errorCode_(error) {
  const message = String(error.message || '');
  return /^(CONFIG_[A-Z_]+|RENDER_[A-Z_0-9]+|BACKUP_[A-Z_]+|DELIVERY_[A-Z_]+)$/.test(message) ? message : 'GOOGLE_OPERATION_FAILED';
}

function status_(component, status, detail) {
  const entry = {at: new Date().toISOString(), component: component, status: status, detail: detail || ''};
  PropertiesService.getScriptProperties().setProperty('STATUS_' + component, JSON.stringify(entry));
  console.log(JSON.stringify(entry));
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('LeadGen data phase')
    .addItem('Refresh dashboard', 'refreshDashboard')
    .addItem('Deliver next final lead batch', 'deliverFinalLeads')
    .addItem('Run/resume daily backup', 'dailyBackup')
    .addItem('Pause pipeline', 'pausePipeline')
    .addItem('Resume pipeline flags', 'resumePipeline')
    .addToUi();
}
