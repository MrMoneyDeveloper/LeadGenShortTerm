/**
 * Outreach draft service (operator decision 2026-11-09). NOTHING HERE SENDS EMAIL.
 *
 * For each company in CONTACT_ROLES_### that has no draft yet and has not unsubscribed, it fetches the
 * company's own public page, builds a short redacted excerpt (title, description, headings, opening
 * text), and asks GroqCloud (free tier) for ONE line describing what the business does, using only that
 * excerpt. The line goes into the fixed, compliance-approved template; everything else in the email
 * stays word-for-word. One EMAIL_DRAFTS_### row per company, status PENDING_REVIEW (or NEEDS_MANUAL when
 * the page was unreachable, too thin, or the line failed validation). Compliance approves each row.
 *
 * Groq lanes: org A then org B, each with the configured models in order (limits are per org and
 * model). Per-lane daily request/token soft caps persist in script properties; a 429 parks that lane
 * until its reset. When every lane is spent, drafting stops and continues on the next run/day.
 * Only the business name and the redacted page excerpt are sent to Groq: never email addresses.
 */
const DRAFT_TAB_ = /^EMAIL_DRAFTS_(\d{3,})$/;
const DRAFT_SHARD_ROWS_ = 5000;
const DRAFT_FIELDS_ = ['lead_id', 'business_name', 'to_email', 'other_mailboxes', 'subject', 'personal_line',
  'interest_url', 'unsubscribe_url', 'website', 'source_excerpt', 'draft_source', 'status', 'approved',
  'reviewer_notes', 'generated_at', 'short_name'];
// Hacked or spam pages: never describe them, send to manual review.
// Insurers, brokers and FSPs are competitors, not leads: never draft them (the collector only saw titles).
const DRAFT_TRADE_ = /(authori[sz]ed financial services provider|FSP\s*(no|number)?\.?\s*\d|insurance broker|insurance brokers|brokers?[^.]{0,40}insur|insur[a-z]*[^.]{0,40}broker|funeral (cover|plan|policy|scheme)|short[- ]term insurance|long[- ]term insurance|life insurance|underwrit|insurer)/i;
const DRAFT_SPAM_ = /\b(slot|slots|casino|gacor|togel|betting|bet365|poker|jackpot|porn|xxx|viagra|cialis|escort|crypto ?casino)\b/i;
const DRAFT_FETCH_GROUP_ = 20;
const DRAFT_SYSTEM_ = 'You write ONE opening line for a short business email from a South African insurance team to the ' +
  'business described below. First give the business\'s short trading name exactly as written in the text (no taglines, ' +
  'no locations). Then write the line TO the business, starting with "We noticed" or "We see", using that short name, ' +
  'then what it does, using ONLY facts stated in the website text. 1-2 sentences, at most 40 words, plain ASCII ' +
  'punctuation. Do not mention insurance, cover, risk, prices, people\'s names, email addresses or phone numbers. ' +
  'Do not invent services, locations, history or claims, and do not flatter. If the text does not clearly say what ' +
  'the business does, or it looks unrelated to the named business, return an empty line. ' +
  'Reply with JSON only: {"name": "...", "line": "...", "confidence": "high|low"}';

function draftSubject_(name) {
  return 'A quick one for ' + String(name || 'your business').slice(0, 80);
}

function draftAscii_(text) {
  return String(text || '').replace(/[\u2010-\u2015\u2212\ufffd]/g, '-').replace(/[\u2018\u2019\u201b]/g, "'")
    .replace(/[\u201c\u201d]/g, '"').replace(/\u2026/g, '...').replace(/\s+/g, ' ').trim();
}

/** Short name the model picked, accepted only if it appears in the page excerpt; else the first title segment. */
function draftShortName_(name, business, excerpt) {
  const clean = draftAscii_(name).replace(/^[\s"'.,-]+|[\s"'.,-]+$/g, '');
  if (clean && clean.length <= 60 && draftAscii_(excerpt).toLowerCase().indexOf(clean.toLowerCase()) >= 0) return clean;
  const parts = draftAscii_(business).split(/\s+[|:-]\s+/).map(x => x.trim()).filter(Boolean);
  return (parts.sort((a, b) => a.length - b.length)[0] || draftAscii_(business)).slice(0, 60);
}

function draftTabs_(book) {
  return book.getSheets().filter(t => DRAFT_TAB_.test(t.getName())).sort((a, b) => a.getName().localeCompare(b.getName()));
}

function columnSet_(book, test, column) {
  const set = {};
  book.getSheets().filter(t => test(t.getName())).forEach(tab => {
    const last = tab.getLastRow();
    if (last < 2) return;
    tab.getRange(2, column, last - 1, 1).getDisplayValues().forEach(r => { if (r[0]) set[r[0]] = true; });
  });
  return set;
}

/** Companies with a contact-role entry, no draft yet, and no unsubscribe. */
function draftPending_(book, limit) {
  const done = columnSet_(book, n => DRAFT_TAB_.test(n), 1);
  const suppressed = columnSet_(book, n => n === 'UNSUBSCRIBE', 1);
  const pending = [];
  for (const tab of roleTabs_(book)) {
    const last = tab.getLastRow();
    if (last < 2) continue;
    const width = tab.getLastColumn();
    const header = tab.getRange(1, 1, 1, width).getDisplayValues()[0];
    const col = name => header.indexOf(name);
    for (const v of tab.getRange(2, 1, last - 1, width).getDisplayValues()) {
      const id = v[col('lead_id')];
      if (!id || done[id] || suppressed[id]) continue;
      pending.push({lead_id: id, business_name: v[col('business_name')], website: v[col('website')],
        to_email: v[col('best_email')], other_mailboxes: v[col('other_mailboxes')]});
      if (pending.length >= limit) return pending;
    }
  }
  return pending;
}

function draftDecode_(text) {
  return String(text || '').replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"')
    .replace(/&#0?39;|&apos;/gi, "'").replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
    .replace(/&#(\d{2,5});/g, (m, n) => { const c = Number(n); return c >= 32 && c < 65536 ? String.fromCharCode(c) : ' '; });
}

function draftRedact_(text) {
  return String(text || '').replace(/\S+@\S+/g, ' ').replace(/https?:\/\/\S+|www\.\S+/gi, ' ')
    .replace(/(?:\+?27|0)[\s\-().]*\d(?:[\s\-().]*\d){7,10}/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Short redacted excerpt of the company's own page; thin when it says too little to describe them. */
function draftExcerpt_(html) {
  const page = String(html || '').slice(0, 600000);
  const pick = re => { const m = re.exec(page); return m ? draftDecode_(m[1]).replace(/<[^>]+>/g, ' ') : ''; };
  const title = pick(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const meta = pick(/<meta[^>]+(?:name|property)=["'](?:og:)?description["'][^>]+content=["']([^"']*)["']/i) ||
    pick(/<meta[^>]+content=["']([^"']*)["'][^>]+(?:name|property)=["'](?:og:)?description["']/i);
  const heads = [];
  page.replace(/<h[12][^>]*>([\s\S]*?)<\/h[12]>/gi, (m, h) => {
    const t = draftDecode_(h.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
    if (t && heads.length < 6 && heads.indexOf(t) < 0) heads.push(t);
    return m;
  });
  const body = draftDecode_(page.replace(/<(script|style|noscript|svg|nav|footer|header)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim().slice(0, 900);
  const parts = [title && 'Title: ' + title, meta && 'Description: ' + meta, heads.length && 'Headings: ' + heads.join(' | '),
    body && 'Text: ' + body].filter(Boolean);
  const excerpt = draftRedact_(parts.join('. ')).slice(0, 1100);
  const words = draftRedact_((meta + ' ' + heads.join(' ') + ' ' + body)).split(' ').filter(w => w.length > 2).length;
  return {excerpt: excerpt, thin: words < 20};
}

/** Validated, ASCII-normalised line, or '' when it breaks a rule. */
function draftCleanLine_(line, name) {
  let s = draftAscii_(line);
  if (!s || s.length > 320) return '';
  if (/@|https?:|www\.|\d{3}[\s-]?\d{3,}/i.test(s)) return '';
  if (/insur|cover|premium|polic(y|ies)|risk/i.test(s) || DRAFT_SPAM_.test(s)) return '';
  if (!/^(we noticed|we see)/i.test(s)) return '';
  // The line must name the business (any distinctive word of its short name).
  const words = draftAscii_(name).toLowerCase().split(/[^a-z0-9&]+/).filter(w => w.length >= 3 &&
    ['the', 'and', 'for', 'pty', 'ltd', 'inc', 'home', 'welcome'].indexOf(w) < 0);
  if (words.length && !words.some(w => s.toLowerCase().indexOf(w) >= 0)) return '';
  if (!/[.!]$/.test(s)) s += '.';
  return s;
}

// ---- Groq lanes and budget --------------------------------------------------------------------------
function draftLanes_() {
  const props = PropertiesService.getScriptProperties();
  const models = (props.getProperty('GROQ_DRAFT_MODELS') || 'openai/gpt-oss-120b,openai/gpt-oss-20b')
    .split(',').map(m => m.trim()).filter(Boolean);
  const lanes = [];
  [['A', 'GROQ_ORG_A_API_KEY'], ['B', 'GROQ_ORG_B_API_KEY']].forEach(([org, name]) => {
    const key = props.getProperty(name);
    if (key) models.forEach(model => lanes.push({org: org, model: model, key: key, id: org + ':' + model}));
  });
  return lanes;
}

function draftBudget_() {
  const props = PropertiesService.getScriptProperties();
  const day = new Date().toISOString().slice(0, 10);
  let budget;
  try { budget = JSON.parse(props.getProperty('DRAFT_BUDGET') || '{}'); } catch (e) { budget = {}; }
  if (budget.day !== day) budget = {day: day, lanes: {}};
  return budget;
}

function draftSaveBudget_(budget) {
  PropertiesService.getScriptProperties().setProperty('DRAFT_BUDGET', JSON.stringify(budget));
}

function draftLaneOpen_(budget, lane) {
  const props = PropertiesService.getScriptProperties();
  const caps = {req: Number(props.getProperty('DRAFT_DAILY_REQUESTS') || 900), tok: Number(props.getProperty('DRAFT_DAILY_TOKENS') || 180000)};
  const state = budget.lanes[lane.id] || {req: 0, tok: 0, until: 0};
  return state.req < caps.req && state.tok < caps.tok && Date.now() >= (state.until || 0);
}

function draftResetMs_(value) {
  const m = /^(?:(\d+)m)?(?:([\d.]+)s)?$/.exec(String(value || ''));
  return m ? (Number(m[1] || 0) * 60 + Number(m[2] || 0)) * 1000 : 60000;
}

/** One Groq call on the first open lane. Returns {line, source} or {line: '', source, spent: true}. */
function draftGroq_(budget, business, excerpt) {
  for (const lane of draftLanes_()) {
    if (!draftLaneOpen_(budget, lane)) continue;
    const state = budget.lanes[lane.id] || (budget.lanes[lane.id] = {req: 0, tok: 0, until: 0});
    let response;
    try {
      response = UrlFetchApp.fetch('https://api.groq.com/openai/v1/chat/completions', {method: 'post',
        contentType: 'application/json', muteHttpExceptions: true, headers: {Authorization: 'Bearer ' + lane.key},
        payload: JSON.stringify({model: lane.model, temperature: 0.4, max_completion_tokens: 300,
          reasoning_effort: 'low', include_reasoning: false, response_format: {type: 'json_object'},
          messages: [{role: 'system', content: DRAFT_SYSTEM_},
            {role: 'user', content: 'Business: ' + business + '. ' + excerpt}]})});
    } catch (e) {
      state.until = Date.now() + 60000;
      continue;
    }
    state.req++;
    const code = response.getResponseCode();
    const headers = response.getAllHeaders ? response.getAllHeaders() : {};
    if (code === 429) {
      const retry = Number(headers['retry-after'] || headers['Retry-After'] || 0);
      state.until = Date.now() + Math.max(retry * 1000, draftResetMs_(headers['x-ratelimit-reset-tokens']), 20000);
      continue;
    }
    if (code !== 200) { state.until = Date.now() + 300000; continue; }
    let body;
    try { body = JSON.parse(response.getContentText()); } catch (e) { continue; }
    state.tok += Number((body.usage || {}).total_tokens || 0);
    let parsed = {};
    try { parsed = JSON.parse(((body.choices || [])[0] || {}).message.content || '{}'); } catch (e) { parsed = {}; }
    const shortName = draftShortName_(parsed.name, business, excerpt);
    return {line: draftCleanLine_(parsed.line, shortName), name: shortName, source: 'groq:' + lane.org + ':' + lane.model,
      confidence: parsed.confidence === 'high' ? 'high' : 'low'};
  }
  return {line: '', source: 'none', spent: true};
}

// ---- writing ------------------------------------------------------------------------------------------
function draftAppend_(book, rows) {
  if (!rows.length) return;
  let tabs = draftTabs_(book);
  let tab = tabs[tabs.length - 1];
  let offset = 0;
  while (offset < rows.length) {
    if (!tab || tab.getLastRow() - 1 >= DRAFT_SHARD_ROWS_) {
      const next = tabs.length ? Number(DRAFT_TAB_.exec(tab.getName())[1]) + 1 : 1;
      tab = book.insertSheet('EMAIL_DRAFTS_' + ('00' + next).slice(-3));
      tab.getRange(1, 1, 1, DRAFT_FIELDS_.length).setValues([DRAFT_FIELDS_]);
      tab.setFrozenRows(1);
      tabs = draftTabs_(book);
    }
    const head = tab.getRange(1, 1, 1, Math.max(1, tab.getLastColumn())).getDisplayValues()[0];
    if (head.length < DRAFT_FIELDS_.length || DRAFT_FIELDS_.some((f, i) => head[i] !== f)) {
      if (tab.getMaxColumns() < DRAFT_FIELDS_.length) tab.insertColumnsAfter(tab.getMaxColumns(), DRAFT_FIELDS_.length - tab.getMaxColumns());
      tab.getRange(1, 1, 1, DRAFT_FIELDS_.length).setValues([DRAFT_FIELDS_]);  // new columns are only ever appended
    }
    const start = Math.max(2, tab.getLastRow() + 1);
    const part = rows.slice(offset, offset + (DRAFT_SHARD_ROWS_ - (start - 2)));
    if (tab.getMaxRows() < start + part.length - 1) tab.insertRowsAfter(tab.getMaxRows(), start + part.length - 1 - tab.getMaxRows());
    if (tab.getMaxColumns() < DRAFT_FIELDS_.length) tab.insertColumnsAfter(tab.getMaxColumns(), DRAFT_FIELDS_.length - tab.getMaxColumns());
    tab.getRange(start, 1, part.length, DRAFT_FIELDS_.length).setValues(part.map(r => DRAFT_FIELDS_.map(f => safeCell_(r[f]))));
    offset += part.length;
  }
}

function draftRow_(lead, line, source, excerpt, note, shortName) {
  const name = shortName || draftShortName_('', lead.business_name, excerpt);
  return {lead_id: lead.lead_id, business_name: draftAscii_(lead.business_name), short_name: name, to_email: lead.to_email,
    other_mailboxes: lead.other_mailboxes, subject: draftSubject_(name), personal_line: line,
    interest_url: interestUrl_(lead.lead_id), unsubscribe_url: unsubscribeUrl_(lead.lead_id), website: lead.website,
    source_excerpt: String(excerpt || '').slice(0, 500), draft_source: source,
    status: line ? 'PENDING_REVIEW' : 'NEEDS_MANUAL', approved: '', reviewer_notes: note || '',
    generated_at: new Date().toISOString()};
}

function draftFetchPages_(leads) {
  const request = l => ({url: l.website, muteHttpExceptions: true, followRedirects: true, validateHttpsCertificates: true});
  let responses = [];
  try {
    responses = UrlFetchApp.fetchAll(leads.map(request));
  } catch (e) {
    // One bad address (DNS failure, malformed URL) makes fetchAll throw for the whole group:
    // fetch one by one so only the failing site is marked unavailable.
    responses = leads.map(l => {
      try { return UrlFetchApp.fetch(l.website, request(l)); } catch (err) { return null; }
    });
  }
  return leads.map((lead, i) => {
    const r = responses[i];
    if (!r || r.getResponseCode() !== 200) return {ok: false};
    return Object.assign({ok: true}, draftExcerpt_(r.getContentText()));
  });
}

/**
 * Draft up to ``limit`` companies within ``seconds``. Each written row is final; a rerun continues
 * with the next pending companies. Returns counts only.
 */
function draftsRun(limit, seconds) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {status: 'busy'};
  try {
    const deadline = Date.now() + 1000 * Math.min(Number(seconds) || 240, 300);
    const book = sheet_();
    const pending = draftPending_(book, Math.min(Number(limit) || 200, 1000));
    const budget = draftBudget_();
    const counts = {written: 0, pending_review: 0, needs_manual: 0, groq_calls: 0};
    let spent = false;
    for (let i = 0; i < pending.length && !spent && Date.now() < deadline; i += DRAFT_FETCH_GROUP_) {
      const group = pending.slice(i, i + DRAFT_FETCH_GROUP_);
      const pages = draftFetchPages_(group);
      const rows = [];
      for (let j = 0; j < group.length; j++) {
        const lead = group[j], page = pages[j];
        if (!page.ok) { rows.push(draftRow_(lead, '', 'none', '', 'page_unavailable')); continue; }
        if (DRAFT_TRADE_.test(page.excerpt)) { rows.push(draftRow_(lead, '', 'none', page.excerpt, 'looks_like_insurer_or_broker')); continue; }
        if (page.thin) { rows.push(draftRow_(lead, '', 'none', page.excerpt, 'page_too_thin')); continue; }
        if (DRAFT_SPAM_.test(page.excerpt)) { rows.push(draftRow_(lead, '', 'none', page.excerpt, 'page_looks_compromised')); continue; }
        if (Date.now() >= deadline) break;
        const result = draftGroq_(budget, lead.business_name, page.excerpt);
        if (result.spent) { spent = true; break; }  // keep this lead pending for the next run
        counts.groq_calls++;
        rows.push(draftRow_(lead, result.line, result.source, page.excerpt,
          result.line ? (result.confidence === 'low' ? 'low_confidence' : '') : 'line_failed_validation', result.name));
        Utilities.sleep(1500);  // stay well inside the free-tier tokens-per-minute limit
      }
      draftAppend_(book, rows);
      rows.forEach(r => { counts.written++; counts[r.status === 'PENDING_REVIEW' ? 'pending_review' : 'needs_manual']++; });
      draftSaveBudget_(budget);
    }
    draftSaveBudget_(budget);
    const more = draftPending_(book, 1).length > 0;
    return {status: 'ok', counts: counts, groq_spent_for_today: spent, more_pending: more};
  } finally {
    lock.releaseLock();
  }
}

/** Excerpts for an operator-run drafting batch (business name + redacted page text, never emails). */
function draftExcerpts_(n) {
  const book = sheet_();
  const pending = draftPending_(book, Math.min(Number(n) || 20, 60));
  const pages = draftFetchPages_(pending);
  return pending.map((lead, i) => ({lead_id: lead.lead_id, business_name: lead.business_name,
    ok: pages[i].ok, thin: !!pages[i].thin, excerpt: pages[i].ok ? pages[i].excerpt : ''}));
}

/** Import operator-written lines (validated by the same rules); unknown or already drafted ids skipped. */
function draftImport_(items, source) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return {status: 'busy'};
  try {
    const book = sheet_();
    const byId = {};
    draftPending_(book, 20000).forEach(l => { byId[l.lead_id] = l; });
    const rows = [], skipped = [];
    (items || []).slice(0, 200).forEach(item => {
      const lead = byId[String(item && item.lead_id)];
      if (!lead) { skipped.push(String(item && item.lead_id)); return; }
      const name = draftShortName_(item.name, lead.business_name, item.excerpt || item.name || '');
      const line = draftCleanLine_(item.line, name);
      rows.push(draftRow_(lead, line, line ? source : 'none', item.excerpt || '', line ? '' : 'line_failed_validation', name));
      delete byId[lead.lead_id];
    });
    draftAppend_(book, rows);
    return {status: 'ok', written: rows.length, pending_review: rows.filter(r => r.status === 'PENDING_REVIEW').length,
      skipped: skipped.length};
  } finally {
    lock.releaseLock();
  }
}

function draftsStatus_() {
  const book = sheet_();
  const count = {};
  draftTabs_(book).forEach(tab => {
    const last = tab.getLastRow();
    if (last < 2) return;
    const header = tab.getRange(1, 1, 1, tab.getLastColumn()).getDisplayValues()[0];
    const col = header.indexOf('status') + 1;
    tab.getRange(2, col, last - 1, 1).getDisplayValues().forEach(r => { count[r[0]] = (count[r[0]] || 0) + 1; });
  });
  return {status: 'ok', drafts: count, budget: draftBudget_(), more_pending: draftPending_(book, 1).length > 0};
}

function draftsPeek_(n) {
  const rows = [];
  draftTabs_(sheet_()).forEach(tab => {
    const last = tab.getLastRow();
    if (last < 2) return;
    const header = tab.getRange(1, 1, 1, tab.getLastColumn()).getDisplayValues()[0];
    const start = Math.max(2, last - n + 1);
    tab.getRange(start, 1, last - start + 1, header.length).getDisplayValues().forEach(v => {
      const r = {};
      ['lead_id', 'short_name', 'personal_line', 'draft_source', 'status', 'reviewer_notes'].forEach(f => { r[f] = v[header.indexOf(f)]; });
      rows.push(r);
    });
  });
  return rows.slice(-n);
}

/** Remove draft rows for these lead ids (never APPROVED ones) so they are drafted again. */
function draftsRedo_(leadIds) {
  const wanted = {};
  (leadIds || []).slice(0, 500).forEach(id => { wanted[String(id)] = true; });
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return {status: 'busy'};
  try {
    let removed = 0;
    draftTabs_(sheet_()).forEach(tab => {
      const last = tab.getLastRow();
      if (last < 2) return;
      const header = tab.getRange(1, 1, 1, tab.getLastColumn()).getDisplayValues()[0];
      const values = tab.getRange(2, 1, last - 1, header.length).getDisplayValues();
      for (let r = values.length - 1; r >= 0; r--) {
        const approved = String(values[r][header.indexOf('approved')] || '').trim().toUpperCase();
        if (wanted[values[r][0]] && approved !== 'YES' && approved !== 'APPROVED') { tab.deleteRow(r + 2); removed++; }
      }
    });
    return {status: 'ok', removed: removed};
  } finally {
    lock.releaseLock();
  }
}
