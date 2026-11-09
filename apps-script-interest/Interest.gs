/**
 * Standalone "I'm interested" web app (operator decision 2026-11-09). Deliberately a SEPARATE Apps
 * Script project from the delivery/ops project: any HtmlService page exposes google.script.run, which
 * can call every top-level function without a trailing underscore. This project therefore has exactly
 * four public functions: doGet, doPost, recordInterest and recordUnsubscribe. Everything else ends in "_".
 *
 * Unsubscribe links use  ?u=1&i=<lead_id>&t=<token>  with token = HMAC("unsub:" + lead_id), so an
 * interest link can never be replayed as an unsubscribe link or the other way round. Unsubscribes are
 * written to the UNSUBSCRIBE tab, which the draft service reads as its suppression list.
 *
 * Link:  <this web app URL>?i=<lead_id>&t=<token>, token = HMAC-SHA256(lead_id, INTEREST_SECRET), 32 hex.
 * The delivery project generates links with the same secret (copied here once by its interest_setup
 * ops action); neither project ever returns the secret.
 *
 * Two steps so link scanners (which open links but never press buttons) cannot create false leads:
 * GET shows a "Yes, contact me" page and records nothing; the button calls recordInterest, which
 * verifies the token again and writes one row per lead to the INTEREST tab. Repeats keep the first.
 * This project's script lock is its own, so it can never delay lead delivery.
 */
const OPS_SECRET_SHA256_ = '7ff92fb612bf4034f488521d064eeaa4138ac86a9cf9aa18fe1731544e52a41b';
const KINDS_ = {
  interest: {tab: 'INTEREST', prefix: '', fields: ['lead_id', 'business_name', 'email', 'website', 'confirmed_at', 'status'],
    status: 'interested'},
  unsubscribe: {tab: 'UNSUBSCRIBE', prefix: 'unsub:',
    fields: ['lead_id', 'business_name', 'email', 'website', 'unsubscribed_at', 'status'], status: 'unsubscribed'},
};
const TAB_ = KINDS_.interest.tab;
const LEAD_ = /^[A-Za-z0-9_-]{1,64}:\d{1,18}$/;
const TOKEN_ = /^[a-f0-9]{32}$/;
const VALIDATED_ = /^VALIDATED_(?:(?:[A-Z]|OTHER)_)?\d{3,}$/;

function hex_(bytes) { return bytes.map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join(''); }

function sha256_(value) {
  return hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(value), Utilities.Charset.UTF_8));
}

function props_() { return PropertiesService.getScriptProperties(); }

function book_() {
  const id = props_().getProperty('SPREADSHEET_ID');
  if (!id) throw new Error('SPREADSHEET_ID_MISSING');
  return SpreadsheetApp.openById(id);
}

function token_(leadId, prefix) {
  const secret = props_().getProperty('INTEREST_SECRET');
  if (!secret) throw new Error('INTEREST_SECRET_MISSING');
  return hex_(Utilities.computeHmacSha256Signature((prefix || '') + leadId, secret)).slice(0, 32);
}

function valid_(leadId, token, prefix) {
  if (typeof leadId !== 'string' || typeof token !== 'string') return false;
  if (!LEAD_.test(leadId) || !TOKEN_.test(token)) return false;
  let expected;
  try { expected = token_(leadId, prefix); } catch (e) { return false; }
  let diff = 0;  // constant-time comparison
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ token.charCodeAt(i);
  return diff === 0;
}

function host_(url) {
  const match = /^https?:\/\/([^/:?#]+)/i.exec(String(url || ''));
  const host = match ? match[1].toLowerCase().replace(/\.$/, '') : '';
  return host.indexOf('www.') === 0 ? host.slice(4) : host;
}

function cell_(value) {
  const s = value === null || value === undefined ? '' : String(value);
  return (/^\s*[=+\-@]/.test(s) || /^[\t\r\n]/.test(s)) ? "'" + s : s;
}

function lookup_(book, leadId) {
  for (const tab of book.getSheets().filter(t => VALIDATED_.test(t.getName()))) {
    const last = tab.getLastRow();
    if (last < 2) continue;
    const width = tab.getLastColumn();
    const header = tab.getRange(1, 1, 1, width).getDisplayValues()[0];
    const col = name => header.indexOf(name);
    if (col('lead_id') < 0) continue;
    const ids = tab.getRange(2, col('lead_id') + 1, last - 1, 1).getDisplayValues();
    for (let r = 0; r < ids.length; r++) {
      if (ids[r][0] !== leadId) continue;
      const v = tab.getRange(r + 2, 1, 1, width).getDisplayValues()[0];
      const host = host_(col('source_url') >= 0 ? v[col('source_url')] : '');
      return {business_name: col('business_name') >= 0 ? v[col('business_name')] : '',
        email: col('email') >= 0 ? v[col('email')] : '', website: host ? 'https://' + host + '/' : ''};
    }
  }
  return null;
}

function tab_(book, kind) {
  const spec = KINDS_[kind];
  let tab = book.getSheetByName(spec.tab);
  if (!tab) {
    tab = book.insertSheet(spec.tab);
    const head = tab.getRange(1, 1, 1, spec.fields.length);
    head.setNumberFormat('@');
    head.setValues([spec.fields]);
    tab.setFrozenRows(1);
  }
  return tab;
}

/** Called by the confirmation page button. Returns only a status word. */
function recordInterest(leadId, token) { return record_('interest', leadId, token); }

/** Called by the unsubscribe page button. Returns only a status word. */
function recordUnsubscribe(leadId, token) { return record_('unsubscribe', leadId, token); }

function record_(kind, leadId, token) {
  const spec = KINDS_[kind];
  if (!valid_(leadId, token, spec.prefix)) return 'invalid';
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(15000)) return 'busy';
  try {
    const book = book_();
    const tab = tab_(book, kind);
    const last = tab.getLastRow();
    if (last >= 2 && tab.getRange(2, 1, last - 1, 1).getDisplayValues().some(r => r[0] === leadId)) return 'recorded';
    const lead = lookup_(book, leadId) || {business_name: '', email: '', website: ''};
    const row = {lead_id: leadId, business_name: lead.business_name, email: lead.email, website: lead.website,
      status: lead.email ? spec.status : spec.status + '_lead_not_found'};
    row[spec.fields[4]] = new Date().toISOString();
    const at = Math.max(2, last + 1);
    if (tab.getMaxRows() < at) tab.insertRowsAfter(tab.getMaxRows(), at - tab.getMaxRows());
    const target = tab.getRange(at, 1, 1, spec.fields.length);
    target.setNumberFormat('@');  // plain text: Sheets must not reinterpret ids or timestamps
    target.setValues([spec.fields.map(f => cell_(row[f]))]);
    return 'recorded';
  } catch (e) {
    return 'error';
  } finally {
    lock.releaseLock();
  }
}

const PAGES_ = {
  interest: {ask: '<h1>Would you like someone to contact you?</h1><p>Press the button and a person will be in touch within one working day to find a time that suits you.</p>',
    button: 'Yes, contact me', done: '<div class="tick" aria-hidden="true">&#10003;</div><h1>Got it. We\\\'ll be in touch.</h1><p>Thanks for letting us know. Someone will contact you within one working day.</p><p>You can close this page.</p>',
    call: 'recordInterest'},
  unsubscribe: {ask: '<h1>Stop emails from us?</h1><p>Press the button and this address will not receive any more emails from us.</p>',
    button: 'Unsubscribe', done: '<div class="tick" aria-hidden="true">&#10003;</div><h1>You\\\'re unsubscribed.</h1><p>This address will not receive any more emails from us. You can close this page.</p>',
    call: 'recordUnsubscribe'},
};

function page_(valid, leadId, token, kind) {
  const spec = PAGES_[kind || 'interest'];
  const data = JSON.stringify({i: valid ? leadId : '', t: valid ? token : ''}).replace(/</g, '\\u003c');
  const body = valid
    ? spec.ask + '<button id="go" type="button">' + spec.button + '</button><p id="msg" class="small" role="status"></p>'
    : '<h1>This link isn\'t valid.</h1><p>It may be incomplete. You can simply reply to the email you received instead.</p>';
  const script = valid ? '<script>const D=' + data + ';const b=document.getElementById("go"),m=document.getElementById("msg");' +
    'b.addEventListener("click",function(){b.disabled=true;m.textContent="One moment...";' +
    'google.script.run.withSuccessHandler(function(r){if(r==="recorded"){document.getElementById("main").innerHTML=' +
    "'" + spec.done + "';}" +
    'else{b.disabled=false;m.textContent=r==="busy"?"Busy right now, please press again.":"That did not work. Please reply to the email instead.";}})' +
    '.withFailureHandler(function(){b.disabled=false;m.textContent="That did not work. Please press again or reply to the email.";})' +
    '.' + spec.call + '(D.i,D.t);});</script>' : '';
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Business cover</title><style>' +
    'body{margin:0;background:#0f2436;color:#e6eff4;font-family:Arial,Helvetica,sans-serif}' +
    '.wrap{max-width:520px;margin:0 auto;padding:64px 22px}' +
    'h1{font-family:Georgia,"Times New Roman",serif;font-size:28px;line-height:1.25;margin:0 0 12px;color:#fff}' +
    'p{font-size:16px;line-height:1.6;color:#b9cddb;margin:0 0 16px}.small{font-size:14px}' +
    'button{background:#f2c14e;color:#0f2436;border:0;border-radius:25px;font:bold 16px Arial,Helvetica,sans-serif;padding:15px 30px;cursor:pointer}' +
    'button:focus-visible{outline:3px solid #fff;outline-offset:3px}button[disabled]{opacity:.6;cursor:default}' +
    '.tick{width:64px;height:64px;border-radius:50%;background:#f2c14e;color:#0f2436;font-size:34px;line-height:64px;text-align:center;font-weight:bold;margin-bottom:22px}' +
    '</style></head><body><main class="wrap" id="main">' + body + '</main>' + script + '</body></html>';
  return HtmlService.createHtmlOutput(html).setTitle('Business cover')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** Public GET: shows the confirmation step for a valid link; never records anything by itself. */
function doGet(e) {
  const p = (e && e.parameter) || {};
  const kind = p.u === '1' ? 'unsubscribe' : 'interest';
  return page_(valid_(p.i, p.t, KINDS_[kind].prefix), p.i, p.t, kind);
}

function out_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

/** Operator-only configuration (same ops secret as the delivery project). Never returns secrets. */
function doPost(e) {
  let request;
  try { request = JSON.parse((e && e.postData && e.postData.contents) || '{}'); } catch (err) { return out_({status: 'bad_request'}); }
  if (typeof request.auth !== 'string' || request.auth.length < 32 || sha256_(request.auth) !== OPS_SECRET_SHA256_) {
    return out_({status: 'unauthorized'});
  }
  try {
    if (request.action === 'configure') {
      if (typeof request.secret !== 'string' || request.secret.length < 48) return out_({status: 'bad_secret'});
      if (!/^[A-Za-z0-9_-]{10,160}$/.test(String(request.spreadsheet_id || ''))) return out_({status: 'bad_spreadsheet'});
      const current = props_().getProperty('INTEREST_SECRET');
      if (current && current !== request.secret && !request.rotate) return out_({status: 'secret_already_set'});
      props_().setProperty('INTEREST_SECRET', request.secret);
      props_().setProperty('SPREADSHEET_ID', request.spreadsheet_id);
      book_().getName();  // proves the deploying account can open the sheet
      return out_({status: 'ok'});
    }
    if (request.action === 'count') {
      const book = book_();
      const rows = name => { const tab = book.getSheetByName(name); return tab ? Math.max(0, tab.getLastRow() - 1) : 0; };
      return out_({status: 'ok', interested: rows(KINDS_.interest.tab), unsubscribed: rows(KINDS_.unsubscribe.tab)});
    }
    return out_({status: 'unknown_action'});
  } catch (err) {
    return out_({status: 'error', code: String(err && err.message || err).replace(/[^A-Z0-9_]/g, '').slice(0, 60)});
  }
}
