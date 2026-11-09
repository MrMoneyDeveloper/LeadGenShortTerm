/** Offline execution of the actual Apps Script functions. No Google or Render requests. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');

function sha(text) { return crypto.createHash('sha256').update(text, 'utf8').digest('hex'); }
function iterator(items) { return {hasNext: () => items.length > 0, next: () => items.shift()}; }
function fixture(items) {
  const batch = {batch_id: 'batch-1', campaign_id: 'phase2', spreadsheet_id: 'sheet-test', drive_folder_id: 'folder-test',
    fields: ['lead_id', 'email', 'display_name'], shard_rows: 5000, status: 'CLAIMED', items: items || [
      {lead_id: 1, tab: 'VALIDATED_001', row: 2, values: ['phase2:1', 'one@example.test', '=literal, "name"']},
      {lead_id: 2, tab: 'VALIDATED_001', row: 3, values: ['phase2:2', 'two@example.test', 'Zoë 😀']}
    ]};
  batch.checksum = sha(JSON.stringify(batch.items));
  return batch;
}

function environment(batch = fixture()) {
  const props = new Map(Object.entries({RENDER_BASE_URL: 'https://test.example', GOOGLE_SPREADSHEET_ID: 'sheet-test',
    GOOGLE_DRIVE_BACKUP_FOLDER_ID: 'folder-test', PROCESSOR_TRIGGER_TOKEN: 'offline-fixture', DASHBOARD_API_TOKEN: 'offline-fixture'}));
  const sheets = new Map(), files = [], requests = [], flags = {};
  let acknowledged = false, claims = 0, acks = 0;
  function tab(name) {
    const values = [], formulas = [], formats = [];
    let maxRows = 1000, maxColumns = 26;
    const object = {name, values, formulas, getName: () => name, getMaxRows: () => maxRows,
      getMaxColumns: () => maxColumns, insertRowsAfter: (_at, n) => {maxRows += n;},
      insertColumnsAfter: (_at, n) => {maxColumns += n;}, setFrozenRows: () => {},
      deleteRows: (at, n) => {assert.ok(at + n - 1 <= maxRows); maxRows -= n;},
      deleteColumns: (at, n) => {assert.ok(at + n - 1 <= maxColumns); maxColumns -= n;},
      clearContents: () => {values.length = 0; formulas.length = 0;},
      getLastRow: () => {let i = values.length; while (i && !(values[i - 1] || []).some(v => v !== '')) i--; return i;},
      getLastColumn: () => values.reduce((max, row) => Math.max(max, row.length), 0),
      getRange: (row, column, height, width) => {
        assert.ok(row >= 1 && column >= 1 && height >= 1 && width >= 1);
        assert.ok(row + height - 1 <= maxRows && column + width - 1 <= maxColumns, 'range exceeds grid');
        function read(source) { return Array.from({length: height}, (_, r) => Array.from({length: width}, (_, c) => String((source[row + r - 1] || [])[column + c - 1] || ''))); }
        function write(rows, literal) {
          rows.forEach((cells, r) => cells.forEach((value, c) => {
            values[row + r - 1] ||= []; formulas[row + r - 1] ||= [];
            let text = String(value);
            if (literal && (formats[row + r - 1] || [])[column + c - 1] !== '@' &&
                /^[+-]?\d+(?:\.\d+)?$/.test(text)) text = String(Number(text));
            values[row + r - 1][column + c - 1] = text;
            formulas[row + r - 1][column + c - 1] = !literal && text.startsWith('=') ? text : '';
          }));
        }
        return {getDisplayValues: () => read(values), getFormulas: () => read(formulas),
          setNumberFormat: format => {
            for (let r = 0; r < height; r++) {
              formats[row + r - 1] ||= [];
              for (let c = 0; c < width; c++) formats[row + r - 1][column + c - 1] = format;
            }
          },
          setValues: rows => write(rows, false), setRichTextValues: rows => write(rows.map(cells => cells.map(value => value.text)), true)};
      }};
    sheets.set(name, object);
    return object;
  }
  const book = {getSheetByName: name => sheets.get(name), insertSheet: tab, getSheets: () => Array.from(sheets.values())};
  const folder = {getFilesByName: name => iterator(files.filter(file => file.name === name)), createFile: blob => {
    if (flags.failBeforeCreate && flags.failBeforeCreate(blob.name)) {flags.failBeforeCreate = null; throw new Error('injected failure');}
    const file = {name: blob.name, content: blob.content, id: 'file-' + (files.length + 1),
      getId() {return this.id;}, getUrl() {return 'https://drive.example/' + this.id;}, isTrashed: () => false,
      getBlob() {return {getDataAsString: () => this.content};}};
    files.push(file);
    if (flags.failAfterCreate) {flags.failAfterCreate = false; throw new Error('injected failure');}
    return file;
  }};
  const context = vm.createContext({console: {log: () => {}}, Date,
    PropertiesService: {getScriptProperties: () => ({getProperty: key => props.get(key) || null,
      setProperty: (key, value) => props.set(key, value), deleteProperty: key => props.delete(key)})},
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
    SpreadsheetApp: {openById: id => {assert.equal(id, 'sheet-test'); return book;},
      newRichTextValue: () => ({setText(text) {this.text = text; return this;}, build() {return {text: this.text};}}),
      flush: () => {if (flags.failFlush) {flags.failFlush = false; throw new Error('injected failure');}}},
    DriveApp: {getFolderById: id => {assert.equal(id, 'folder-test'); return folder;}, getFileById: id => {
      const file = files.find(file => file.id === id); assert.ok(file); return file;
    }},
    Utilities: {DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
      computeDigest: (_algorithm, text) => Array.from(crypto.createHash('sha256').update(text, 'utf8').digest()).map(x => x > 127 ? x - 256 : x),
      newBlob: (content, mime, name) => ({content, mime, name}), formatDate: () => '2026-09-13', sleep: () => {}}
  });
  for (const name of ['Code.gs', 'Backups.gs', 'Dashboard.gs', 'Delivery.gs', 'Roles.gs', 'InterestLinks.gs', 'Drafts.gs', 'Controls.gs']) {
    vm.runInContext(fs.readFileSync('apps-script/' + name, 'utf8'), context, {filename: name});
  }
  context.api_ = (path, method, body, operator) => {
    requests.push({path, method, body, operator});
    let result;
    if (path === '/exports/claim') {assert.equal(operator, true); claims++; result = acknowledged ? {status: 'empty', items: []} : batch;}
    else if (path === '/exports/ack') {
      acks++;
      assert.equal(operator, true); assert.equal(body.checksum, batch.checksum); assert.equal(body.drive_checksum, batch.checksum);
      assert.equal(body.spreadsheet_id, 'sheet-test'); assert.equal(body.drive_folder_id, 'folder-test');
      assert.deepEqual(JSON.parse(JSON.stringify(body.placements)), batch.items.map(({lead_id, tab, row}) => ({lead_id, tab, row})));
      assert.ok(files.some(file => file.id === body.drive_file_id));
      if (flags.failAck) {flags.failAck = false; throw new Error('RENDER_HTTP_503');}
      acknowledged = true;
      if (flags.loseAckResponse) {flags.loseAckResponse = false; throw new Error('RENDER_NETWORK_UNAVAILABLE');}
      result = {status: 'ACKNOWLEDGED', deleted_leads: batch.items.length};
    } else if (path === '/dashboard/summary') result = {pipeline: 'paused'};
    else if (path === '/dashboard/source-stats' || path === '/dashboard/failures') result = [];
    else if (path === '/dashboard/queue') result = {recent_jobs: []};
    else if (path === '/exports/source-stats') return {getContentText: () => 'source,count\r\nsynthetic,2\r\n'};
    else if (path === '/exports/processing-summary') result = {raw: 0, validated: 0};
    else throw new Error('Unexpected endpoint ' + path);
    return {getContentText: () => JSON.stringify(result)};
  };
  return {context, props, sheets, files, requests, flags, tab, batch, stats: () => ({claims, acks, acknowledged})};
}

let passed = 0;
function test(name, run) {run(); passed++; console.log('PASS ' + name);}

test('delivery preserves numeric-looking text exactly through Google readback and retry', () => {
  const e = environment(fixture([
    {lead_id: 1, tab: 'VALIDATED_001', row: 2, values: ['phase2:1', '0.00', '00123']},
    {lead_id: 2, tab: 'VALIDATED_001', row: 3, values: ['phase2:2', '10.0', '+0123']}
  ]));
  e.context.deliverFinalLeads();
  assert.equal(e.stats().acknowledged, true);
  assert.deepEqual(e.sheets.get('VALIDATED_001').values.slice(1), e.batch.items.map(item => item.values));
  e.context.deliverFinalLeads();
  assert.equal(e.stats().acks, 1);
});

test('SHA256 and CSV literal protection', () => {
  const e = environment();
  assert.equal(e.context.sha256_('abc'), sha('abc'));
  assert.equal(e.context.csvRows_([['=SUM(A1)', 'a"b', 'Zoë']]), '"\'=SUM(A1)","a""b","Zoë"\r\n');
});
test('delivery writes literal fields, verifies Drive and ACKs once', () => {
  const e = environment(); e.context.deliverFinalLeads();
  assert.equal(e.stats().acknowledged, true);
  assert.equal(e.files.length, 2);
  assert.equal(e.sheets.get('VALIDATED_001').values[1][2], '=literal, "name"');
  assert.equal(e.sheets.get('VALIDATED_001').formulas[1][2], '');
  assert.equal(e.props.has('DELIVERY_STATE'), false);
  e.context.deliverFinalLeads();
  assert.equal(e.stats().acks, 1); assert.equal(e.files.length, 2);
  assert.equal(e.sheets.get('EXPORT_INDEX').getLastRow(), 2);
});
for (const [name, flag] of [['Sheet write before flush', 'failFlush'], ['Drive write before checkpoint', 'failAfterCreate'],
  ['ACK rejection', 'failAck'], ['lost ACK response', 'loseAckResponse']]) {
  test('retry after ' + name, () => {
    const e = environment(); e.flags[flag] = true;
    assert.throws(() => e.context.deliverFinalLeads());
    const saved = e.props.get('DELIVERY_STATE') || '';
    assert.equal(saved.includes('example.test'), false, 'personal data in properties');
    e.context.deliverFinalLeads();
    assert.equal(e.stats().acknowledged, true); assert.equal(e.files.length, 2);
    assert.equal(e.sheets.get('VALIDATED_001').getLastRow(), 3);
    assert.equal(e.sheets.get('EXPORT_INDEX').getLastRow(), 2);
    if (flag === 'loseAckResponse' || flag === 'failAck') assert.equal(e.stats().claims, 1, 'claimed new batch before retrying old ACK');
  });
}
test('rollover writes 5000th and 5001st records into separate shards', () => {
  const e = environment(fixture([
    {lead_id: 5000, tab: 'VALIDATED_001', row: 5001, values: ['phase2:5000', 'a@example.test', 'A']},
    {lead_id: 5001, tab: 'VALIDATED_002', row: 2, values: ['phase2:5001', 'b@example.test', 'B']}
  ]));
  e.context.deliverFinalLeads();
  assert.equal(e.sheets.get('VALIDATED_001').values[5000][0], 'phase2:5000');
  assert.equal(e.sheets.get('VALIDATED_002').values[1][0], 'phase2:5001');
});
test('server-pinned shard size controls rollover and rejects out-of-bounds rows', () => {
  const batch = fixture([
    {lead_id: 3, tab: 'VALIDATED_001', row: 4, values: ['phase2:3', 'a@example.test', 'A']},
    {lead_id: 4, tab: 'VALIDATED_002', row: 2, values: ['phase2:4', 'b@example.test', 'B']}
  ]);
  batch.shard_rows = 3;
  const e = environment(batch); e.context.deliverFinalLeads();
  assert.equal(e.sheets.get('VALIDATED_001').values[3][0], 'phase2:3');
  assert.equal(e.sheets.get('VALIDATED_002').values[1][0], 'phase2:4');
  assert.equal(JSON.parse(e.files.find(file => file.name.endsWith('.json')).content).shard_rows, 3);
  const invalid = fixture([{lead_id: 4, tab: 'VALIDATED_001', row: 5, values: ['phase2:4', 'x@example.test', 'X']}]);
  invalid.shard_rows = 3;
  const rejected = environment(invalid);
  assert.throws(() => rejected.context.deliverFinalLeads(), /DELIVERY_PLACEMENT_INVALID/);
  assert.equal(rejected.stats().acks, 0);
});
test('source names and evidence follow the server field schema without fabrication', () => {
  const batch = fixture();
  batch.fields.push('first_name', 'salutation', 'source_evidence');
  batch.items[0].values.push('Zoë', 'Hi Zoë', 'Public display name: Zoë');
  batch.items[1].values.push('', 'Hello', 'No reliable first name');
  batch.checksum = sha(JSON.stringify(batch.items));
  const e = environment(batch); e.context.deliverFinalLeads();
  assert.deepEqual(JSON.parse(JSON.stringify(e.sheets.get('VALIDATED_001').values[0])), batch.fields);
  assert.equal(e.sheets.get('VALIDATED_001').values[1][3], 'Zoë');
  assert.equal(e.sheets.get('VALIDATED_001').values[2][3], '');
  assert.equal(e.sheets.get('VALIDATED_001').values[2][4], 'Hello');
});
test('conflicting existing row or checksum prevents ACK', () => {
  const e = environment(); const tab = e.tab('VALIDATED_001');
  tab.getRange(2, 1, 1, 3).setValues([['foreign:id', 'untouched', 'X']]);
  assert.throws(() => e.context.deliverFinalLeads(), /DELIVERY_ROW_CONFLICT/);
  assert.equal(tab.values[1][0], 'foreign:id'); assert.equal(e.stats().acks, 0);
  const bad = environment(); bad.batch.items[0].values[1] = 'changed';
  assert.throws(() => bad.context.deliverFinalLeads(), /DELIVERY_BATCH_INVALID/);
  assert.equal(bad.stats().acks, 0);
});
test('dashboard repeat preserves final shards and export index', () => {
  const e = environment(); e.context.deliverFinalLeads();
  const rows = JSON.stringify(e.sheets.get('VALIDATED_001').values);
  e.context.refreshDashboard(); e.context.refreshDashboard();
  assert.equal(JSON.stringify(e.sheets.get('VALIDATED_001').values), rows);
  assert.equal(e.sheets.get('EXPORT_INDEX').getLastRow(), 2);
  assert.equal(e.requests.some(request => request.path.includes('/exports/validated')), false);
});
test('daily backup reads final Sheets after database drain and reuses all files', () => {
  const e = environment(); e.context.deliverFinalLeads(); e.context.dailyBackup();
  const n = e.files.length; e.context.dailyBackup();
  assert.equal(e.files.length, n);
  const csv = e.files.find(file => file.name.startsWith('validated-leads-') && file.name.endsWith('.csv'));
  assert.ok(csv.content.includes('one@example.test'));
  const manifest = JSON.parse(e.files.find(file => file.name === 'backup-manifest-2026-09-13.json').content);
  assert.equal(manifest.complete, true); assert.equal(manifest.shards[0].through, 3);
  assert.equal(manifest.statistics_files.length, 2);
  assert.equal(e.sheets.get('EXPORT_HISTORY').getLastRow(), 2);
});
test('backup resumes after CSV creation before sidecar checkpoint', () => {
  const e = environment(); e.context.deliverFinalLeads();
  e.flags.failBeforeCreate = name => name.endsWith('.csv.json');
  assert.throws(() => e.context.dailyBackup());
  e.context.dailyBackup();
  assert.equal(e.files.filter(file => file.name.startsWith('validated-leads-') && file.name.endsWith('.csv')).length, 1);
  assert.equal(JSON.parse(e.props.get('BACKUP_STATE')).complete, true);
});
test('backup repairs history after manifest write and bounds CSV pages', () => {
  const e = environment(); const tab = e.tab('VALIDATED_001');
  tab.insertRowsAfter(1000, 5);
  tab.getRange(1, 1, 1, 2).setValues([['lead_id', 'email']]);
  tab.getRange(2, 1, 1001, 2).setValues(Array.from({length: 1001}, (_, n) => ['phase2:' + (n + 1), 'fixture@example.test']));
  const original = e.context.exportHistory_; let inject = true;
  e.context.exportHistory_ = (state, status, url) => {
    if (inject && status === 'SUCCESS') {inject = false; throw new Error('history unavailable');}
    return original(state, status, url);
  };
  assert.throws(() => e.context.dailyBackup());
  e.context.dailyBackup();
  assert.equal(e.files.filter(file => file.name.startsWith('validated-leads-') && file.name.endsWith('.csv')).length, 2);
  assert.equal(e.sheets.get('EXPORT_HISTORY').values[1][1], 'SUCCESS');
  assert.equal(e.sheets.get('EXPORT_HISTORY').getLastRow(), 2);
});
function alphabetical() {
  return fixture([
    {lead_id: 11, tab: 'VALIDATED_A_001', row: 2, values: ['phase2:11', 'alice@example.test', 'A']},
    {lead_id: 12, tab: 'VALIDATED_A_001', row: 3, values: ['phase2:12', 'amy@example.test', 'A2']},
    {lead_id: 13, tab: 'VALIDATED_Z_001', row: 2, values: ['phase2:13', 'zed@example.test', 'Z']},
    {lead_id: 14, tab: 'VALIDATED_OTHER_001', row: 2, values: ['phase2:14', '9lives@example.test', 'O']},
  ]);
}
test('alphabetical delivery writes per-letter and OTHER tabs at exact grid size', () => {
  const e = environment(alphabetical());
  e.context.deliverFinalLeads();
  assert.equal(e.stats().acks, 1);
  assert.deepEqual(e.sheets.get('VALIDATED_A_001').values.slice(1).map(row => row[0]), ['phase2:11', 'phase2:12']);
  assert.equal(e.sheets.get('VALIDATED_Z_001').values[1][0], 'phase2:13');
  assert.equal(e.sheets.get('VALIDATED_OTHER_001').values[1][0], 'phase2:14');
  const a = e.sheets.get('VALIDATED_A_001');
  assert.equal(a.getMaxRows(), 3, 'header + 2 rows, no empty 1000-row grid');
  assert.equal(a.getMaxColumns(), 3, 'one column per field, no empty 26-column grid');
  assert.equal(e.sheets.get('VALIDATED_001'), undefined, 'legacy sequential tabs are not touched');
});
test('alphabetical rollover continues in the next shard of the same letter', () => {
  const batch = fixture([
    {lead_id: 21, tab: 'VALIDATED_B_001', row: 4, values: ['phase2:21', 'bob@example.test', 'B']},
    {lead_id: 22, tab: 'VALIDATED_B_002', row: 2, values: ['phase2:22', 'bea@example.test', 'B']},
  ]);
  batch.shard_rows = 3;
  const e = environment(batch); e.context.deliverFinalLeads();
  assert.equal(e.sheets.get('VALIDATED_B_001').values[3][0], 'phase2:21');
  assert.equal(e.sheets.get('VALIDATED_B_002').values[1][0], 'phase2:22');
});
test('invalid bucket tab names are rejected before any write', () => {
  for (const tab of ['VALIDATED_a_001', 'VALIDATED_AB_001', 'VALIDATED_OTHERS_001', 'VALIDATED_A_1', 'LEADS_A_001']) {
    const e = environment(fixture([{lead_id: 1, tab, row: 2, values: ['phase2:1', 'x@example.test', 'X']}]));
    assert.throws(() => e.context.deliverFinalLeads(), /DELIVERY_PLACEMENT_INVALID/, tab);
    assert.equal(e.stats().acks, 0);
  }
});
test('workbook capacity is checked before creating tabs, writing or ACKing', () => {
  const e = environment(alphabetical());
  e.props.set('WORKBOOK_CELL_LIMIT', '100000');
  const filler = e.tab('EXISTING_DATA');
  filler.insertRowsAfter(1000, 2500);  // 3,500 x 26 = 91,000 allocated cells (> 90% of 100,000)
  assert.throws(() => e.context.deliverFinalLeads(), /DELIVERY_WORKBOOK_CAPACITY/);
  assert.equal(e.sheets.get('VALIDATED_A_001'), undefined);
  assert.equal(e.stats().acks, 0);
  e.props.set('WORKBOOK_CELL_LIMIT', '10000000');
  e.context.deliverFinalLeads();  // the same claimed batch delivers once capacity exists
  assert.equal(e.stats().acks, 1);
});
test('shard index lists every validated tab, bucket, rows, capacity and allocated cells', () => {
  const e = environment(alphabetical());
  e.context.deliverFinalLeads();
  const index = e.sheets.get('SHARD_INDEX').values;
  assert.deepEqual(index[0], ['tab', 'bucket', 'data_rows', 'capacity', 'remaining', 'allocated_cells']);
  const rows = Object.fromEntries(index.slice(1).map(row => [row[0], row]));
  assert.deepEqual(rows.VALIDATED_A_001.slice(1, 6), ['A', '2', '5000', '4998', '9']);
  assert.equal(rows.VALIDATED_OTHER_001[1], 'OTHER');
  assert.equal(rows.WORKBOOK_TOTAL[2], '4');
});
test('alphabetical lost ACK replays the same batch without duplicate rows', () => {
  const e = environment(alphabetical());
  e.flags.loseAckResponse = true;
  assert.throws(() => e.context.deliverFinalLeads());
  e.context.deliverFinalLeads();
  assert.equal(e.stats().claims, 1);
  assert.equal(e.sheets.get('VALIDATED_A_001').getLastRow(), 3);
  assert.equal(e.files.filter(file => file.name.endsWith('.json')).length, 1);
});
test('daily backup includes alphabetical and legacy shards in a stable order', () => {
  const e = environment(alphabetical());
  e.context.deliverFinalLeads();
  const legacy = e.tab('VALIDATED_001');
  legacy.getRange(1, 1, 2, 3).setValues([['lead_id', 'email', 'display_name'], ['phase2:1', 'legacy@example.test', 'L']]);
  assert.deepEqual(e.context.backupSnapshot_().map(item => item.tab),
    ['VALIDATED_001', 'VALIDATED_A_001', 'VALIDATED_OTHER_001', 'VALIDATED_Z_001']);
});
test('no-email prospects go to PROSPECTS tabs, indexed and backed up separately', () => {
  const e = environment(fixture([
    {lead_id: 31, tab: 'VALIDATED_C_001', row: 2, values: ['phase2:31', 'cat@example.test', 'C']},
    {lead_id: 32, tab: 'PROSPECTS_001', row: 2, values: ['phase2:32', '', 'Prospect']},
  ]));
  e.context.deliverFinalLeads();
  assert.equal(e.sheets.get('PROSPECTS_001').values[1][0], 'phase2:32');
  const index = Object.fromEntries(e.sheets.get('SHARD_INDEX').values.slice(1).map(row => [row[0], row]));
  assert.equal(index.PROSPECTS_001[1], 'PROSPECTS');
  assert.deepEqual(e.context.backupSnapshot_().map(item => item.tab), ['VALIDATED_C_001', 'PROSPECTS_001']);
  assert.throws(() => e.context.writeRows_('PROSPECTS_001', [{a: 1}]), /DELIVERY_APPEND_ONLY_TAB/);
  for (const tab of ['PROSPECTS_1', 'PROSPECT_001', 'PROSPECTS_A_001']) {
    const bad = environment(fixture([{lead_id: 1, tab, row: 2, values: ['phase2:1', '', 'X']}]));
    assert.throws(() => bad.context.deliverFinalLeads(), /DELIVERY_PLACEMENT_INVALID/, tab);
  }
});
function businessTab(e, name, leads) {
  const tab = e.tab(name);
  tab.values.push(['lead_id', 'email', 'business_name', 'source_url', 'product_type']);
  leads.forEach(l => tab.values.push([l.id, l.email, l.name, l.url, l.product || 'BUSINESS_CONTACT']));
  return tab;
}

function pages(e, byUrl) {
  const fetched = [];
  e.context.UrlFetchApp = {fetchAll: requests => requests.map(r => {
    fetched.push(r.url);
    const html = byUrl[r.url];
    return {getResponseCode: () => (html === undefined ? 404 : 200), getContentText: () => html || ''};
  })};
  return fetched;
}

test('contact roles: separate entries, best generic mailbox, never named people', () => {
  const e = environment();
  businessTab(e, 'VALIDATED_A_001', [
    {id: 'cc:1', email: 'info@acme.co.za', name: 'Acme', url: 'https://www.acme.co.za/'},
    {id: 'cc:2', email: 'sales@beta.co.za', name: 'Beta', url: 'https://beta.co.za/contact'},
    {id: 'cc:3', email: 'info@gone.co.za', name: 'Gone', url: 'https://gone.co.za/'},
    {id: 'p:4', email: 'person@example.com', name: '', url: 'https://bsky.app/x', product: 'MOTOR'},
  ]);
  const before = JSON.stringify(e.sheets.get('VALIDATED_A_001').values);
  const fetched = pages(e, {
    'https://www.acme.co.za/': 'info@acme.co.za | accounts@acme.co.za | jane.doe@acme.co.za | info@co.za | admin@other.co.za',
    'https://beta.co.za/contact': 'Sales: sales@beta.co.za, support@beta.co.za',
  });
  const result = e.context.contactRoles(100, 60);
  assert.equal(result.status, 'ok'); assert.equal(result.more_pending, false);
  assert.deepEqual(JSON.parse(JSON.stringify(result.counts)), {written: 3, checked: 2, page_unavailable: 1, improved: 1});
  assert.equal(fetched.length, 3);  // consumer rows are never touched
  const rows = e.sheets.get('CONTACT_ROLES_001').values;
  const by = Object.fromEntries(rows.slice(1).map(r => [r[0], Object.fromEntries(rows[0].map((h, i) => [h, r[i]]))]));
  assert.equal(by['cc:1'].best_email, 'accounts@acme.co.za'); assert.equal(by['cc:1'].best_role, 'Accounts');
  assert.equal(by['cc:1'].delivered_role, 'General'); assert.equal(by['cc:1'].other_mailboxes, 'info@acme.co.za');
  assert.ok(!JSON.stringify(rows).includes('jane.doe') && !JSON.stringify(rows).includes('other.co.za'));
  assert.ok(!JSON.stringify(rows).includes('info@co.za'));
  assert.equal(by['cc:2'].best_email, 'sales@beta.co.za'); assert.equal(by['cc:2'].other_mailboxes, 'support@beta.co.za');
  assert.equal(by['cc:3'].status, 'page_unavailable'); assert.equal(by['cc:3'].best_email, 'info@gone.co.za');
  assert.equal(by['cc:1'].website, 'https://acme.co.za/');
  assert.equal(JSON.stringify(e.sheets.get('VALIDATED_A_001').values), before);  // delivered rows unchanged
  const again = e.context.contactRoles(100, 60);
  assert.equal(again.counts.written, 0);  // rerun never duplicates an entry
  assert.equal(e.sheets.get('CONTACT_ROLES_001').getLastRow(), 4);
});

test('contact roles roll over at 5000 entries and resume across runs', () => {
  const e = environment();
  const leads = Array.from({length: 5003}, (_, i) => ({id: 'cc:' + (i + 1), email: 'info@s' + i + '.co.za', name: 'S' + i,
    url: 'https://s' + i + '.co.za/'}));
  e.tab('VALIDATED_S_001').getRange(1, 1, 1, 1);  // ensure grid grows like the real sheet
  const tab = businessTab(e, 'VALIDATED_S_002', leads);
  tab.insertRowsAfter(0, 5000);
  pages(e, {});
  let first = e.context.contactRoles(2000, 60);
  assert.equal(first.counts.written, 2000); assert.equal(first.more_pending, true);
  while (first.more_pending) first = e.context.contactRoles(2000, 60);
  assert.equal(e.sheets.get('CONTACT_ROLES_001').getLastRow(), 5001);
  assert.equal(e.sheets.get('CONTACT_ROLES_002').getLastRow(), 4);
  const ids = new Set();
  for (const name of ['CONTACT_ROLES_001', 'CONTACT_ROLES_002']) e.sheets.get(name).values.slice(1).forEach(r => ids.add(r[0]));
  assert.equal(ids.size, 5003);
});

test('contact role mailbox list matches the collector configuration', () => {
  const yaml = fs.readFileSync('config/sources.yaml', 'utf8');
  const block = yaml.slice(yaml.indexOf('business_locals:'));
  const locals = [];
  for (const line of block.split('\n').slice(1)) {
    const m = /^\s+-\s+(\S+)\s*$/.exec(line);
    if (!m) break;
    locals.push(m[1]);
  }
  const e = environment();
  assert.ok(locals.length > 10);
  assert.deepEqual(Object.keys(vm.runInContext('ROLE_LOCALS_', e.context)).sort(), locals.slice().sort());
});

const OPS_SECRET = 'ops-secret-for-offline-tests-0123456789abcdef';

function hmacUtilities(target) {
  let uuid = 0;
  Object.assign(target, {
    getUuid: () => 'uuid-' + (++uuid) + '-0123456789abcdef0123',
    computeHmacSha256Signature: (value, key) => {
      if (typeof value !== 'string' || typeof key !== 'string') throw new Error('Utilities.computeHmacSha256Signature: string args only');
      return Array.from(crypto.createHmac('sha256', key).update(value, 'utf8').digest()).map(x => x > 127 ? x - 256 : x);
    },
  });
}

/** Delivery project (main harness env) plus the separate interest project sharing one spreadsheet. */
function interestEnv() {
  const e = environment();
  hmacUtilities(e.context.Utilities);
  businessTab(e, 'VALIDATED_A_001', [{id: 'cc:7', email: 'info@acme.co.za', name: 'Acme', url: 'https://www.acme.co.za/'}]);
  const iprops = new Map();
  const book = {getSheetByName: name => e.sheets.get(name), insertSheet: e.tab, getSheets: () => Array.from(e.sheets.values()),
    getName: () => 'sheet'};
  const utilities = {DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
    computeDigest: (_a, text) => Array.from(crypto.createHash('sha256').update(text, 'utf8').digest()).map(x => x > 127 ? x - 256 : x)};
  hmacUtilities(utilities);
  const ctx = vm.createContext({Date, JSON,
    PropertiesService: {getScriptProperties: () => ({getProperty: k => iprops.get(k) || null, setProperty: (k, v) => iprops.set(k, v)})},
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
    SpreadsheetApp: {openById: id => { assert.equal(id, 'sheet-test'); return book; }},
    Utilities: utilities,
    HtmlService: {createHtmlOutput: html => ({html, setTitle() { return this; }, addMetaTag() { return this; }})},
    ContentService: {MimeType: {JSON: 'json'}, createTextOutput: text => ({text, setMimeType() { return this; }})},
  });
  const source = fs.readFileSync('apps-script-interest/Interest.gs', 'utf8').replace(
    /const OPS_SECRET_SHA256_ = '[a-f0-9]{64}';/, "const OPS_SECRET_SHA256_ = '" + sha(OPS_SECRET) + "';");
  vm.runInContext(source, ctx, {filename: 'apps-script-interest/Interest.gs'});
  const base = 'https://script.google.com/macros/s/ABC_123-x/exec';
  // The delivery project's interest_setup posts the shared secret to the interest web app.
  e.context.UrlFetchApp = {fetch: (url, options) => {
    assert.equal(url, base);
    const out = ctx.doPost({postData: {contents: options.payload}});
    return {getContentText: () => out.text, getResponseCode: () => 200};
  }};
  const setup = e.context.interestSetup_(base, OPS_SECRET);
  assert.equal(setup.status, 'ok');
  return {e, ctx, iprops, base};
}

test('interest: link from the delivery project verifies in the separate interest project; two steps; once per lead', () => {
  const {e, ctx, iprops} = interestEnv();
  assert.equal(iprops.get('INTEREST_SECRET'), e.props.get('INTEREST_SECRET'));
  const url = e.context.interestUrl_('cc:7');
  const m = /^https:\/\/script\.google\.com\/macros\/s\/ABC_123-x\/exec\?i=cc%3A7&t=([a-f0-9]{32})$/.exec(url);
  assert.ok(m, url);
  const token = m[1];
  const page = ctx.doGet({parameter: {i: 'cc:7', t: token}}).html;
  assert.ok(page.includes('Yes, contact me') && page.includes('recordInterest'));
  assert.equal(e.sheets.get('INTEREST'), undefined);  // opening the link (a scanner) records nothing
  assert.equal(ctx.recordInterest('cc:7', token), 'recorded');
  const first = e.sheets.get('INTEREST').values[1].slice();
  assert.equal(ctx.recordInterest('cc:7', token), 'recorded');
  assert.equal(e.sheets.get('INTEREST').getLastRow(), 2);
  assert.deepEqual(e.sheets.get('INTEREST').values[1], first);
  const row = Object.fromEntries(e.sheets.get('INTEREST').values[0].map((h, i) => [h, first[i]]));
  assert.equal(row.business_name, 'Acme'); assert.equal(row.email, 'info@acme.co.za');
  assert.equal(row.website, 'https://acme.co.za/'); assert.equal(row.status, 'interested');
  const count = JSON.parse(ctx.doPost({postData: {contents: JSON.stringify({action: 'count', auth: OPS_SECRET})}}).text);
  assert.equal(count.interested, 1);
});

test('interest: forged, tampered, non-string or malformed input is refused and records nothing', () => {
  const {e, ctx} = interestEnv();
  const token = /t=([a-f0-9]{32})/.exec(e.context.interestUrl_('cc:7'))[1];
  const other = /t=([a-f0-9]{32})/.exec(e.context.interestUrl_('cc:8'))[1];
  const cases = [['cc:7', other], ['cc:8', token], ['cc:7', token.slice(0, 31) + (token[31] === 'a' ? 'b' : 'a')],
    ['cc:7', ''], ['', token], ['<script>', token], ['cc:7', token.toUpperCase()], [['cc:7'], token], [[['cc:7']], token],
    ['cc:7', [token]], [{toString: () => 'cc:7'}, token], [undefined, undefined]];
  for (const [i, t] of cases) {
    assert.equal(ctx.recordInterest(i, t), 'invalid', JSON.stringify([i, t]));
    const page = ctx.doGet({parameter: {i, t}}).html;
    assert.ok(page.includes("This link isn") && !page.includes('recordInterest'), JSON.stringify(i));
  }
  assert.ok(ctx.doGet(undefined).html.includes("This link isn"));
  assert.equal(e.sheets.get('INTEREST'), undefined);
  for (const body of [{action: 'count'}, {action: 'count', auth: 'wrong-secret-wrong-secret-wrong-secret'}, {action: 'configure', auth: 'x'}]) {
    assert.equal(JSON.parse(ctx.doPost({postData: {contents: JSON.stringify(body)}}).text).status, 'unauthorized');
  }
  const rotate = JSON.parse(ctx.doPost({postData: {contents: JSON.stringify({action: 'configure', auth: OPS_SECRET,
    secret: 'a'.repeat(60), spreadsheet_id: 'sheet-test'})}}).text);
  assert.equal(rotate.status, 'secret_already_set');  // a configured secret is never silently replaced
});

test('interest: only doGet, doPost, recordInterest and recordUnsubscribe are public in the interest project', () => {
  const source = fs.readFileSync('apps-script-interest/Interest.gs', 'utf8');
  const publicFns = [...source.matchAll(/^function\s+([A-Za-z0-9_$]+)\s*\(/gm)].map(m => m[1]).filter(n => !n.endsWith('_'));
  assert.deepEqual(publicFns.sort(), ['doGet', 'doPost', 'recordInterest', 'recordUnsubscribe']);
});

test('unsubscribe: separate signed link, two steps, one row; interest and unsubscribe tokens never interchange', () => {
  const {e, ctx} = interestEnv();
  const unsub = e.context.unsubscribeUrl_('cc:7');
  const m = /\?u=1&i=cc%3A7&t=([a-f0-9]{32})$/.exec(unsub);
  assert.ok(m, unsub);
  const utoken = m[1];
  const itoken = /t=([a-f0-9]{32})$/.exec(e.context.interestUrl_('cc:7'))[1];
  assert.notEqual(utoken, itoken);
  const page = ctx.doGet({parameter: {u: '1', i: 'cc:7', t: utoken}}).html;
  assert.ok(page.includes('Stop emails from us?') && page.includes('recordUnsubscribe'));
  assert.equal(e.sheets.get('UNSUBSCRIBE'), undefined);
  assert.ok(ctx.doGet({parameter: {u: '1', i: 'cc:7', t: itoken}}).html.includes("This link isn"));
  assert.ok(ctx.doGet({parameter: {i: 'cc:7', t: utoken}}).html.includes("This link isn"));
  assert.equal(ctx.recordUnsubscribe('cc:7', itoken), 'invalid');
  assert.equal(ctx.recordInterest('cc:7', utoken), 'invalid');
  assert.equal(ctx.recordUnsubscribe('cc:7', utoken), 'recorded');
  assert.equal(ctx.recordUnsubscribe('cc:7', utoken), 'recorded');
  assert.equal(e.sheets.get('UNSUBSCRIBE').getLastRow(), 2);
  const row = Object.fromEntries(e.sheets.get('UNSUBSCRIBE').values[0].map((h, i) => [h, e.sheets.get('UNSUBSCRIBE').values[1][i]]));
  assert.equal(row.email, 'info@acme.co.za'); assert.equal(row.status, 'unsubscribed'); assert.ok(row.unsubscribed_at);
  assert.equal(e.sheets.get('INTEREST'), undefined);
  const count = JSON.parse(ctx.doPost({postData: {contents: JSON.stringify({action: 'count', auth: OPS_SECRET})}}).text);
  assert.equal(count.unsubscribed, 1); assert.equal(count.interested, 0);
});

function draftsEnv(pagesByUrl, groq) {
  const env = interestEnv();
  const {e} = env;
  const roles = e.tab('CONTACT_ROLES_001');
  roles.values.push(['lead_id', 'business_name', 'website', 'delivered_email', 'delivered_role', 'best_email', 'best_role',
    'other_mailboxes', 'checked_url', 'status', 'checked_at']);
  [['cc:1', 'Acme Plumbing', 'https://acme.co.za/', 'accounts@acme.co.za', 'info@acme.co.za'],
    ['cc:2', 'Beta Bakery', 'https://beta.co.za/', 'info@beta.co.za', ''],
    ['cc:3', 'Gone CC', 'https://gone.co.za/', 'info@gone.co.za', ''],
    ['cc:4', 'Thin', 'https://thin.co.za/', 'info@thin.co.za', ''],
    ['cc:5', 'Unsub Ltd', 'https://unsub.co.za/', 'info@unsub.co.za', '']].forEach(([id, n, w, best, other]) =>
    roles.values.push([id, n, w, best, 'General', best, 'General', other, w, 'checked', 'x']));
  const un = e.tab('UNSUBSCRIBE');
  un.values.push(['lead_id', 'business_name', 'email', 'website', 'unsubscribed_at', 'status'], ['cc:5', 'Unsub Ltd', '', '', 'x', 'unsubscribed']);
  const prompts = [];
  e.props.set('GROQ_ORG_A_API_KEY', 'key-a'); e.props.set('GROQ_ORG_B_API_KEY', 'key-b');
  e.context.Utilities.sleep = () => {};
  e.context.UrlFetchApp = {
    fetchAll: requests => requests.map(r => {
      const html = pagesByUrl[r.url];
      return {getResponseCode: () => (html === undefined ? 404 : 200), getContentText: () => html || ''};
    }),
    fetch: (url, options) => {
      assert.equal(url, 'https://api.groq.com/openai/v1/chat/completions');
      const body = JSON.parse(options.payload);
      prompts.push({key: options.headers.Authorization, model: body.model, user: body.messages[1].content});
      return groq(prompts.length, body, options);
    },
  };
  return Object.assign(env, {prompts});
}

const PAGE_ACME = '<title>Acme Plumbing | Durban</title><meta name="description" content="Residential and commercial plumbing in Durban, geyser installs, burst pipes and 24-hour call-outs. Call 031 555 1234 or mail info@acme.co.za">' +
  '<h1>Plumbers you can trust</h1><p>Family business since 1998 serving Durban North and Umhlanga homes and offices with plumbing repairs.</p>';
const PAGE_BETA = '<title>Beta Bakery</title><meta name="description" content="Fresh bread, cakes and custom wedding cakes baked daily in Pietermaritzburg for shops, cafes and families across the region.">';

function groqOk(line, name) {
  return () => ({getResponseCode: () => 200, getAllHeaders: () => ({}),
    getContentText: () => JSON.stringify({usage: {total_tokens: 400}, choices: [{message: {content: JSON.stringify({name, line, confidence: 'high'})}}]})});
}

test('drafts: hacked pages, unrelated lines and messy titles are handled', () => {
  const spam = '<title>Social Responsibility | Cranefield College</title><meta name="description" content="IBETSLOT slot gacor easy deposits, big wins and fast withdrawals every day for all players online now">';
  const {e} = draftsEnv({'https://acme.co.za/': spam}, groqOk('We see IBETSLOT is a slot website offering easy deposits'));
  e.context.draftsRun(1, 60);
  const row = e.sheets.get('EMAIL_DRAFTS_001').values[1];
  assert.equal(row[11], 'NEEDS_MANUAL'); assert.equal(row[13], 'page_looks_compromised');
  // A line that does not name the business is rejected even when it passes the other rules.
  assert.equal(e.context.draftCleanLine_('We see a lovely company doing great work in Durban', 'Acme Plumbing'), '');
  // Short name: the model's pick only if it appears in the page; otherwise the shortest title segment.
  assert.equal(e.context.draftShortName_('123 Kids', 'Nursery School | Kempton Park | 123 Kids', 'Title: Nursery School | 123 Kids'), '123 Kids');
  assert.equal(e.context.draftShortName_('Invented Name', 'Nursery School | Kempton Park | 123 Kids', 'Title: x'), '123 Kids');
  assert.equal(e.context.draftShortName_('', '15cm Projects � Specialist Energy', ''), '15cm Projects');
  assert.equal(e.context.draftSubject_('123 Kids'), 'A quick one for 123 Kids');
});

test('drafts: one failing address never marks the rest of its group unavailable', () => {
  const {e} = draftsEnv({'https://acme.co.za/': PAGE_ACME, 'https://beta.co.za/': PAGE_BETA}, groqOk(''));
  const fetchAll = e.context.UrlFetchApp.fetchAll;
  e.context.UrlFetchApp.fetchAll = () => { throw new Error('Address unavailable: https://gone.co.za/'); };
  e.context.UrlFetchApp.fetch = (url) => {
    if (url === 'https://gone.co.za/') throw new Error('Address unavailable');
    return fetchAll([{url}])[0];
  };
  const items = e.context.draftExcerpts_(10);
  const ok = Object.fromEntries(items.map(i => [i.lead_id, i.ok]));
  assert.deepEqual(JSON.parse(JSON.stringify(ok)), {'cc:1': true, 'cc:2': true, 'cc:3': false, 'cc:4': false});
});

test('drafts: insurer, broker and FSP pages are never drafted', () => {
  for (const page of ['<title>Umsizi Wemizi</title><meta name="description" content="Affinity Funeral provides affordable funeral cover to all South Africans with simple plans for every family.">',
    '<title>Home | Afscot Financial Services</title><meta name="description" content="AFSCOT FINANCIAL SERVICES cc Authorised Financial Services Provider - FSP No. 13625 for all your Short Term Insurance needs and hospitality.">']) {
    const {e} = draftsEnv({'https://acme.co.za/': page}, groqOk('We see Acme sells things', 'Acme'));
    e.context.draftsRun(1, 60);
    const row = e.sheets.get('EMAIL_DRAFTS_001').values[1];
    assert.equal(row[11], 'NEEDS_MANUAL'); assert.equal(row[13], 'looks_like_insurer_or_broker');
  }
  // A plumber that mentions being insured is still drafted.
  assert.ok(!vm.runInContext('DRAFT_TRADE_', draftsEnv({}, groqOk('')).e.context).test('Fully insured, qualified plumbers in Durban.'));
});

test('drafts: redo removes non-approved rows so they are drafted again', () => {
  const {e} = draftsEnv({'https://acme.co.za/': PAGE_ACME}, groqOk('We see Acme Plumbing fixes geysers and burst pipes in Durban', 'Acme Plumbing'));
  e.context.draftsRun(1, 60);
  assert.equal(e.sheets.get('EMAIL_DRAFTS_001').getLastRow(), 2);
  e.sheets.get('EMAIL_DRAFTS_001').deleteRow = function (at) { this.values.splice(at - 1, 1); };
  assert.equal(e.context.draftsRedo_(['cc:1']).removed, 1);
  assert.equal(e.context.draftPending_(e.context.sheet_(), 10)[0].lead_id, 'cc:1');
});

test('drafts: one row per company, Groq sees only name + redacted page text, unsubscribed and done are skipped', () => {
  const {e, prompts} = draftsEnv({'https://acme.co.za/': PAGE_ACME, 'https://beta.co.za/': PAGE_BETA, 'https://thin.co.za/': '<title>Home</title>'},
    (n, body) => (body.messages[1].content.includes('Beta')
      ? groqOk('We see Beta Bakery bakes fresh bread and custom wedding cakes daily in Pietermaritzburg', 'Beta Bakery')
      : groqOk('We see Acme Plumbing handles residential and commercial plumbing around Durban, including geyser installs and 24-hour call-outs', 'Acme Plumbing'))());
  const result = e.context.draftsRun(100, 60);
  assert.equal(result.status, 'ok'); assert.equal(result.more_pending, false);
  assert.deepEqual(JSON.parse(JSON.stringify(result.counts)), {written: 4, pending_review: 2, needs_manual: 2, groq_calls: 2});
  for (const p of prompts) {
    assert.ok(!/@|031 555|https?:/.test(p.user), p.user);  // no email, phone or URL ever reaches Groq
  }
  assert.ok(prompts[0].user.startsWith('Business: Acme Plumbing.') && prompts[0].user.includes('geyser'));
  const tab = e.sheets.get('EMAIL_DRAFTS_001').values;
  const rows = Object.fromEntries(tab.slice(1).map(r => [r[0], Object.fromEntries(tab[0].map((h, i) => [h, r[i]]))]));
  assert.deepEqual(Object.keys(rows).sort(), ['cc:1', 'cc:2', 'cc:3', 'cc:4']);  // cc:5 unsubscribed
  assert.equal(rows['cc:1'].status, 'PENDING_REVIEW'); assert.equal(rows['cc:1'].to_email, 'accounts@acme.co.za');
  assert.ok(rows['cc:1'].personal_line.endsWith('call-outs.'));
  assert.ok(/\?i=cc%3A1&t=[a-f0-9]{32}$/.test(rows['cc:1'].interest_url));
  assert.ok(/\?u=1&i=cc%3A1&t=[a-f0-9]{32}$/.test(rows['cc:1'].unsubscribe_url));
  assert.equal(rows['cc:1'].subject, 'A quick one for Acme Plumbing'); assert.equal(rows['cc:1'].approved, '');
  assert.equal(rows['cc:3'].status, 'NEEDS_MANUAL'); assert.equal(rows['cc:3'].reviewer_notes, 'page_unavailable');
  assert.equal(rows['cc:4'].reviewer_notes, 'page_too_thin');
  assert.equal(e.context.draftsRun(100, 60).counts.written, 0);  // rerun never duplicates
});

test('drafts: lines that break the rules are not used', () => {
  const bad = ['We provide plumbing in Durban.', 'We see Acme offers great insurance cover.', 'We see Acme, call 031 555 1234.',
    'We noticed Acme at info@acme.co.za.', ''];
  for (const line of bad) {
    const {e} = draftsEnv({'https://acme.co.za/': PAGE_ACME}, groqOk(line));
    e.context.draftsRun(1, 60);
    const r = e.sheets.get('EMAIL_DRAFTS_001').values[1];
    assert.equal(r[11], 'NEEDS_MANUAL', line); assert.equal(r[5], '', line);
  }
  const {e} = draftsEnv({}, groqOk(''));
  assert.equal(e.context.draftCleanLine_('We see Acme does geyser installs – fast', 'Acme'), 'We see Acme does geyser installs - fast.');
});

test('drafts: 429 parks a lane and moves on; all lanes spent stops without writing the pending company', () => {
  let calls = 0;
  const {e, prompts} = draftsEnv({'https://acme.co.za/': PAGE_ACME, 'https://beta.co.za/': PAGE_BETA}, (n, body, options) => {
    calls++;
    if (options.headers.Authorization === 'Bearer key-a') {
      return {getResponseCode: () => 429, getAllHeaders: () => ({'retry-after': '30'}), getContentText: () => '{}'};
    }
    return groqOk('We see Beta Bakery bakes fresh bread and custom wedding cakes daily in Pietermaritzburg')();
  });
  e.context.draftsRun(100, 60);
  assert.ok(prompts.some(p => p.key === 'Bearer key-b'));  // org B used once org A was rate-limited
  const budget = JSON.parse(e.props.get('DRAFT_BUDGET'));
  assert.ok(budget.lanes['A:openai/gpt-oss-120b'].until > Date.now());
  // Everything spent: daily caps reached on every lane -> nothing written for the remaining company.
  const env2 = draftsEnv({'https://acme.co.za/': PAGE_ACME}, groqOk('We see Acme Plumbing fixes pipes in Durban'));
  env2.e.props.set('DRAFT_DAILY_REQUESTS', '0');
  const spent = env2.e.context.draftsRun(100, 60);
  assert.equal(spent.groq_spent_for_today, true); assert.equal(spent.more_pending, true);
  assert.ok(!env2.e.sheets.get('EMAIL_DRAFTS_001') || env2.e.sheets.get('EMAIL_DRAFTS_001').getLastRow() <= 1 ||
    !env2.e.sheets.get('EMAIL_DRAFTS_001').values.slice(1).some(r => r[0] === 'cc:1'));
});

test('drafts: operator batch import uses the same rules and never duplicates', () => {
  const {e} = draftsEnv({'https://acme.co.za/': PAGE_ACME}, groqOk(''));
  const items = e.context.draftExcerpts_(10);
  assert.ok(items.every(i => !JSON.stringify(i).includes('@')));
  const r = e.context.draftImport_([{lead_id: 'cc:1', line: 'We see Acme Plumbing handles plumbing repairs around Durban North'},
    {lead_id: 'cc:2', line: 'We provide cakes'}, {lead_id: 'cc:5', line: 'We see Unsub Ltd'}, {lead_id: 'nope:1', line: 'x'}], 'operator:claude');
  assert.equal(r.written, 2); assert.equal(r.pending_review, 1); assert.equal(r.skipped, 2);
  assert.equal(e.context.draftImport_([{lead_id: 'cc:1', line: 'We see Acme again'}], 'operator:claude').written, 0);
});

test('delivery project never serves a web page (google.script.run would expose its functions)', () => {
  for (const name of fs.readdirSync('apps-script').filter(f => f.endsWith('.gs'))) {
    const source = fs.readFileSync('apps-script/' + name, 'utf8');
    assert.ok(!/function\s+doGet\s*\(/.test(source) && !/HtmlService/.test(source), name);
  }
});

test('interest secret is created once; links refuse a foreign base URL', () => {
  const {e} = interestEnv();
  const secret = e.props.get('INTEREST_SECRET');
  e.context.interestSecret_(true);
  assert.equal(e.props.get('INTEREST_SECRET'), secret);
  assert.equal(e.context.interestSetup_('https://evil.example/exec', OPS_SECRET).status, 'bad_base_url');
  e.props.set('INTEREST_BASE_URL', 'https://evil.example/exec');
  assert.throws(() => e.context.interestUrl_('cc:7'), /INTEREST_BASE_URL_INVALID/);
});

console.log('apps-script tests: ' + passed + ' passed (offline mocks only)');
