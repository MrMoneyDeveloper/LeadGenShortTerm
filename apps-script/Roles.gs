/**
 * Contact-role service (operator decision 2026-11-09), run after a business-contact campaign.
 *
 * For each delivered BUSINESS_CONTACT lead it writes ONE separate entry in CONTACT_ROLES_### tabs,
 * linked by lead_id: the role of the delivered company mailbox and the most relevant generic company
 * mailbox published on the company's own page (accounts/admin before general before sales...).
 *
 * Only configured generic mailboxes on the company's own .za domain are considered; addresses that
 * name a person are never read into the sheet. Existing VALIDATED_* rows are never modified. Lead
 * data stays in Google: the ops endpoint returns counts only. Rerunning skips lead_ids already done.
 */
// Must equal config/sources.yaml commoncrawl.business_locals (checked by a repository test).
const ROLE_LOCALS_ = {
  info: 'General', general: 'General', contact: 'General', contactus: 'General', hello: 'General', mail: 'General',
  enquiries: 'Enquiries', enquiry: 'Enquiries', enquire: 'Enquiries',
  accounts: 'Accounts', admin: 'Administration', office: 'Administration', reception: 'Reception',
  sales: 'Sales', orders: 'Sales', quotes: 'Sales', marketing: 'Marketing',
  bookings: 'Bookings', reservations: 'Bookings', service: 'Service', support: 'Service',
};
// Most relevant first for a business-insurance conversation: whoever handles the company's accounts
// and administration, then the general enquiries desk, then sales and service desks.
const ROLE_PRIORITY_ = ['Accounts', 'Administration', 'Enquiries', 'General', 'Reception', 'Sales', 'Bookings',
  'Service', 'Marketing'];
const ROLE_FIELDS_ = ['lead_id', 'business_name', 'website', 'delivered_email', 'delivered_role', 'best_email',
  'best_role', 'other_mailboxes', 'checked_url', 'status', 'checked_at'];
const ROLE_TAB_ = /^CONTACT_ROLES_(\d{3,})$/;
const ROLE_SHARD_ROWS_ = 5000;
const ROLE_FETCH_GROUP_ = 50;
const ROLE_ZA_SUFFIXES_ = ['za', 'co.za', 'org.za', 'ac.za', 'gov.za', 'net.za', 'web.za', 'nom.za', 'edu.za',
  'law.za', 'mil.za', 'school.za', 'alt.za', 'ngo.za', 'nis.za', 'tm.za', 'grondar.za', 'agric.za'];
const ROLE_EMAIL_ = /(?<![\w.+-])([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])/g;

function roleHost_(url) {
  const match = /^https?:\/\/([^/:?#]+)/i.exec(String(url || ''));
  const host = match ? match[1].toLowerCase().replace(/\.$/, '') : '';
  return host.indexOf('www.') === 0 ? host.slice(4) : host;
}

function roleOwnDomain_(domain, host) {
  if (ROLE_ZA_SUFFIXES_.indexOf(domain) >= 0) return false;
  return domain === host || host.endsWith('.' + domain) || domain.endsWith('.' + host);
}

function roleOf_(email) {
  return ROLE_LOCALS_[String(email || '').split('@')[0].toLowerCase()] || '';
}

/** Generic company mailboxes on the site's own .za domain, in page order; never a named person. */
function roleMailboxes_(html, host) {
  const found = [];
  let match;
  ROLE_EMAIL_.lastIndex = 0;
  while ((match = ROLE_EMAIL_.exec(html)) !== null) {
    const local = match[1].toLowerCase(), domain = match[2].toLowerCase().replace(/\.$/, '');
    if (!ROLE_LOCALS_[local] || !/\.za$/.test(domain) || !roleOwnDomain_(domain, host)) continue;
    const address = local + '@' + domain;
    if (found.indexOf(address) < 0) found.push(address);
    if (found.length >= 10) break;
  }
  return found;
}

function roleBest_(mailboxes) {
  return mailboxes.slice().sort((a, b) => {
    const ra = ROLE_PRIORITY_.indexOf(roleOf_(a)), rb = ROLE_PRIORITY_.indexOf(roleOf_(b));
    return (ra < 0 ? 99 : ra) - (rb < 0 ? 99 : rb) || mailboxes.indexOf(a) - mailboxes.indexOf(b);
  })[0];
}

function roleTabs_(book) {
  return book.getSheets().filter(t => ROLE_TAB_.test(t.getName()))
    .sort((a, b) => a.getName().localeCompare(b.getName()));
}

function roleDone_(book) {
  const done = {};
  roleTabs_(book).forEach(tab => {
    const last = tab.getLastRow();
    if (last < 2) return;
    tab.getRange(2, 1, last - 1, 1).getDisplayValues().forEach(r => { if (r[0]) done[r[0]] = true; });
  });
  return done;
}

/** Delivered business leads without a contact-role entry yet, in tab order. */
function rolePending_(book, done, limit) {
  const pending = [];
  const tabs = book.getSheets().filter(t => /^VALIDATED_/.test(t.getName()) && VALIDATED_TAB_.test(t.getName()))
    .sort((a, b) => a.getName().localeCompare(b.getName()));
  for (const tab of tabs) {
    const last = tab.getLastRow();
    if (last < 2) continue;
    const width = tab.getLastColumn();
    const header = tab.getRange(1, 1, 1, width).getDisplayValues()[0];
    const col = name => header.indexOf(name);
    const need = ['lead_id', 'email', 'business_name', 'source_url', 'product_type'].map(col);
    if (need.some(i => i < 0)) continue;
    const rows = tab.getRange(2, 1, last - 1, width).getDisplayValues();
    for (const v of rows) {
      if (v[col('product_type')] !== 'BUSINESS_CONTACT' || !v[col('lead_id')] || done[v[col('lead_id')]]) continue;
      pending.push({lead_id: v[col('lead_id')], email: v[col('email')], business_name: v[col('business_name')],
        source_url: v[col('source_url')]});
      if (pending.length >= limit) return pending;
    }
  }
  return pending;
}

function roleAppend_(book, rows) {
  let tabs = roleTabs_(book);
  let tab = tabs[tabs.length - 1];
  let offset = 0;
  while (offset < rows.length) {
    if (!tab || tab.getLastRow() - 1 >= ROLE_SHARD_ROWS_) {
      const next = tabs.length ? Number(ROLE_TAB_.exec(tab.getName())[1]) + 1 : 1;
      tab = book.insertSheet('CONTACT_ROLES_' + ('00' + next).slice(-3));
      tab.getRange(1, 1, 1, ROLE_FIELDS_.length).setValues([ROLE_FIELDS_]);
      tab.setFrozenRows(1);
      tabs = roleTabs_(book);
    }
    const start = Math.max(2, tab.getLastRow() + 1);
    const room = ROLE_SHARD_ROWS_ + 1 - start + 1;
    const part = rows.slice(offset, offset + room);
    if (tab.getMaxRows() < start + part.length - 1) tab.insertRowsAfter(tab.getMaxRows(), start + part.length - 1 - tab.getMaxRows());
    if (tab.getMaxColumns() < ROLE_FIELDS_.length) tab.insertColumnsAfter(tab.getMaxColumns(), ROLE_FIELDS_.length - tab.getMaxColumns());
    tab.getRange(start, 1, part.length, ROLE_FIELDS_.length)
      .setValues(part.map(r => ROLE_FIELDS_.map(f => safeCell_(r[f]))));
    offset += part.length;
  }
}

/**
 * Process up to ``limit`` pending leads within ``seconds``. Each written entry is final; a rerun
 * continues with the next pending leads. Returns counts only.
 */
function contactRoles(limit, seconds) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {status: 'busy'};
  try {
    const deadline = Date.now() + 1000 * Math.min(Number(seconds) || 240, 300);
    const book = sheet_();
    const pending = rolePending_(book, roleDone_(book), Math.min(Number(limit) || 500, 2000));
    const counts = {written: 0, checked: 0, page_unavailable: 0, improved: 0};
    for (let i = 0; i < pending.length && Date.now() < deadline; i += ROLE_FETCH_GROUP_) {
      const group = pending.slice(i, i + ROLE_FETCH_GROUP_);
      let responses = [];
      try {
        responses = UrlFetchApp.fetchAll(group.map(lead => ({url: lead.source_url, muteHttpExceptions: true,
          followRedirects: true, validateHttpsCertificates: true})));
      } catch (e) { responses = []; }
      const rows = group.map((lead, j) => {
        const host = roleHost_(lead.source_url);
        const response = responses[j];
        const ok = response && response.getResponseCode() === 200;
        const page = ok ? roleMailboxes_(String(response.getContentText()).slice(0, 2000000), host) : [];
        const mailboxes = [lead.email].concat(page.filter(m => m !== lead.email));
        const best = roleBest_(mailboxes);
        counts[ok ? 'checked' : 'page_unavailable']++;
        if (best !== lead.email) counts.improved++;
        return {lead_id: lead.lead_id, business_name: lead.business_name, website: host ? 'https://' + host + '/' : '',
          delivered_email: lead.email, delivered_role: roleOf_(lead.email), best_email: best, best_role: roleOf_(best),
          other_mailboxes: mailboxes.filter(m => m !== best).join(', '), checked_url: lead.source_url,
          status: ok ? 'checked' : 'page_unavailable', checked_at: new Date().toISOString()};
      });
      roleAppend_(book, rows);
      counts.written += rows.length;
    }
    const remaining = rolePending_(book, roleDone_(book), 1).length;
    return {status: 'ok', counts: counts, more_pending: remaining > 0};
  } finally {
    lock.releaseLock();
  }
}
