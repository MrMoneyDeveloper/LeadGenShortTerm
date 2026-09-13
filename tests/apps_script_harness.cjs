/** Offline execution of the actual Apps Script functions. No Google or Render requests. */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');

function sha(text) { return crypto.createHash('sha256').update(text, 'utf8').digest('hex'); }
function iterator(items) { return {hasNext: () => items.length > 0, next: () => items.shift()}; }
function fixture(items) {
  const batch = {batch_id: 'batch-1', campaign_id: 'phase2', spreadsheet_id: 'sheet-test', drive_folder_id: 'folder-test',
    fields: ['lead_id', 'email', 'display_name'], status: 'CLAIMED', items: items || [
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
    const values = [], formulas = [];
    let maxRows = 1000, maxColumns = 26;
    const object = {name, values, formulas, getName: () => name, getMaxRows: () => maxRows,
      getMaxColumns: () => maxColumns, insertRowsAfter: (_at, n) => {maxRows += n;},
      insertColumnsAfter: (_at, n) => {maxColumns += n;}, setFrozenRows: () => {},
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
            const text = String(value);
            values[row + r - 1][column + c - 1] = text;
            formulas[row + r - 1][column + c - 1] = !literal && text.startsWith('=') ? text : '';
          }));
        }
        return {getDisplayValues: () => read(values), getFormulas: () => read(formulas),
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
  for (const name of ['Code.gs', 'Backups.gs', 'Dashboard.gs', 'Delivery.gs']) {
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
console.log('apps-script tests: ' + passed + ' passed (offline mocks only)');
