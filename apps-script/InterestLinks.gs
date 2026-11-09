/**
 * Signed "I'm interested" links for outreach drafts. The public page itself lives in the separate
 * apps-script-interest project: this delivery/ops project must never serve a web page (any served
 * page exposes google.script.run to its public functions). Everything here is private ("_").
 */
const INTEREST_LEAD_ = /^[A-Za-z0-9_-]{1,64}:\d{1,18}$/;
const INTEREST_BASE_ = /^https:\/\/script\.google\.com\/macros\/s\/[A-Za-z0-9_-]+\/exec$/;

function interestSecret_(create) {
  const props = PropertiesService.getScriptProperties();
  let secret = props.getProperty('INTEREST_SECRET');
  if (!secret && create) {
    secret = Utilities.getUuid() + Utilities.getUuid();
    props.setProperty('INTEREST_SECRET', secret);
  }
  return secret || '';
}

function interestToken_(leadId) {
  const secret = interestSecret_(false);
  if (!secret) throw new Error('INTEREST_SECRET_MISSING');
  return Utilities.computeHmacSha256Signature(String(leadId), secret)
    .map(b => ('0' + (b & 0xff).toString(16)).slice(-2)).join('').slice(0, 32);
}

function interestUrl_(leadId) {
  if (!INTEREST_LEAD_.test(String(leadId || ''))) throw new Error('INTEREST_LEAD_INVALID');
  const base = PropertiesService.getScriptProperties().getProperty('INTEREST_BASE_URL') || '';
  if (!INTEREST_BASE_.test(base)) throw new Error('INTEREST_BASE_URL_INVALID');
  return base + '?i=' + encodeURIComponent(leadId) + '&t=' + interestToken_(leadId);
}

/** Signed one-click-plus-confirm unsubscribe link (different HMAC domain from interest links). */
function unsubscribeUrl_(leadId) {
  if (!INTEREST_LEAD_.test(String(leadId || ''))) throw new Error('INTEREST_LEAD_INVALID');
  const base = PropertiesService.getScriptProperties().getProperty('INTEREST_BASE_URL') || '';
  if (!INTEREST_BASE_.test(base)) throw new Error('INTEREST_BASE_URL_INVALID');
  return base + '?u=1&i=' + encodeURIComponent(leadId) + '&t=' + interestToken_('unsub:' + leadId);
}

/** Bind the interest web app and give it the same signing secret, authorised by the ops secret. */
function interestSetup_(baseUrl, opsSecret) {
  if (!INTEREST_BASE_.test(String(baseUrl || ''))) return {status: 'bad_base_url'};
  const secret = interestSecret_(true);
  const response = UrlFetchApp.fetch(baseUrl, {method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    followRedirects: true, payload: JSON.stringify({action: 'configure', auth: opsSecret, secret: secret,
      spreadsheet_id: config_().sheet})});
  let body = {};
  try { body = JSON.parse(response.getContentText()); } catch (e) { body = {status: 'non_json', code: response.getResponseCode()}; }
  if (body.status === 'ok') PropertiesService.getScriptProperties().setProperty('INTEREST_BASE_URL', baseUrl);
  return {status: body.status || 'error', code: body.code};
}

function interestCount_(opsSecret) {
  const base = PropertiesService.getScriptProperties().getProperty('INTEREST_BASE_URL') || '';
  if (!INTEREST_BASE_.test(base)) return {status: 'not_configured'};
  const response = UrlFetchApp.fetch(base, {method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    followRedirects: true, payload: JSON.stringify({action: 'count', auth: opsSecret})});
  try { return JSON.parse(response.getContentText()); } catch (e) { return {status: 'non_json'}; }
}
