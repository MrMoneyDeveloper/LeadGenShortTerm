'use strict';
// Offline checks for apps-script/Ops.gs. No Google or network access.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');

const SECRET = 's'.repeat(48);
const HASH = crypto.createHash('sha256').update(SECRET, 'utf8').digest('hex');

function load(now) {
  const props = new Map([['RENDER_BASE_URL', 'https://render.example'], ['PROCESSOR_TRIGGER_TOKEN', 'operator-token-value'],
    ['DELIVERY_ENABLED', 'false']]);
  const triggers = [];
  const context = vm.createContext({
    console: {log: () => {}}, JSON,
    Date: class extends Date { static now() { return now; } },
    PropertiesService: {getScriptProperties: () => ({getProperty: k => (props.has(k) ? props.get(k) : null),
      setProperty: (k, v) => props.set(k, v), deleteProperty: k => props.delete(k)})},
    ContentService: {MimeType: {JSON: 'json'}, createTextOutput: text => ({text, setMimeType() { return this; }})},
    ScriptApp: {getProjectTriggers: () => triggers.slice(), deleteTrigger: t => triggers.splice(triggers.indexOf(t), 1),
      newTrigger: name => ({timeBased: () => ({everyMinutes: () => ({create: () => triggers.push({getHandlerFunction: () => name})}),
        everyHours: () => ({create: () => triggers.push({getHandlerFunction: () => name})})})})},
    SpreadsheetApp: {openById: () => { throw new Error('CONFIG_SPREADSHEET_ID'); }},
    Utilities: {DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'utf8'},
      computeDigest: (_a, text) => Array.from(crypto.createHash('sha256').update(text, 'utf8').digest()).map(x => (x > 127 ? x - 256 : x))},
  });
  for (const name of ['Code.gs', 'Controls.gs']) vm.runInContext(fs.readFileSync('apps-script/' + name, 'utf8'), context);
  const ops = fs.readFileSync('apps-script/Ops.gs', 'utf8').replace(/const OPS_SECRET_SHA256 = '[^']*';/, `const OPS_SECRET_SHA256 = '${HASH}';`);
  vm.runInContext(ops, context);
  const post = body => JSON.parse(context.doPost({postData: {contents: JSON.stringify(body)}}).text);
  return {post, props, triggers, context};
}

const before = Date.parse('2026-11-10T00:00:00Z');
let passed = 0;
function check(name, fn) { fn(); passed++; console.log('PASS ' + name); }

check('wrong, short or missing secret is unauthorized', () => {
  const {post} = load(before);
  assert.equal(post({action: 'status', secret: 'x'.repeat(48)}).status, 'unauthorized');
  assert.equal(post({action: 'status', secret: 'short'}).status, 'unauthorized');
  assert.equal(post({action: 'status'}).status, 'unauthorized');
});

check('expired endpoint refuses even a valid secret', () => {
  const {post} = load(Date.parse('2026-11-15T00:00:01Z'));
  assert.equal(post({action: 'status', secret: SECRET}).status, 'expired');
});

check('malformed body is rejected', () => {
  const {context} = load(before);
  assert.equal(JSON.parse(context.doPost({postData: {contents: '{nope'}}).text).status, 'bad_request');
});

check('status reports flags and hash matches without leaking secret values', () => {
  const {post} = load(before);
  const expected = crypto.createHash('sha256').update('operator-token-value', 'utf8').digest('hex');
  const result = post({action: 'status', secret: SECRET, expect: {PROCESSOR_TRIGGER_TOKEN: expected}});
  assert.equal(result.properties.PROCESSOR_TRIGGER_TOKEN.matches, true);
  assert.equal(result.properties.PROCESSOR_TRIGGER_TOKEN.value, undefined);
  assert.equal(result.properties.DELIVERY_ENABLED.value, 'false');
  assert.ok(!JSON.stringify(result).includes('operator-token-value'));
});

check('configure only accepts whitelisted properties', () => {
  const {post, props} = load(before);
  assert.equal(post({action: 'configure', secret: SECRET, set: {EVIL: '1'}}).status, 'property_not_allowed');
  assert.equal(props.has('EVIL'), false);
  assert.equal(post({action: 'configure', secret: SECRET, set: {DELIVERY_ENABLED: 'true'}, remove: ['DELIVERY_EXPIRES_AT']}).status, 'ok');
  assert.equal(props.get('DELIVERY_ENABLED'), 'true');
});

check('trigger installation respects the existing property gates', () => {
  const {post} = load(before);
  assert.equal(post({action: 'install_triggers', secret: SECRET, triggers: ['delivery']}).code, 'CONFIG_DELIVERY_DISABLED');
  post({action: 'configure', secret: SECRET, set: {DELIVERY_ENABLED: 'true'}});
  assert.deepEqual(post({action: 'install_triggers', secret: SECRET, triggers: ['delivery', 'reporting']}).triggers,
    ['deliveryTick', 'dashboardTick', 'backupTick']);
  assert.deepEqual(post({action: 'remove_triggers', secret: SECRET}).triggers, []);
});

check('unknown actions are refused', () => {
  const {post} = load(before);
  assert.equal(post({action: 'eval', secret: SECRET}).status, 'unknown_action');
});

console.log(`apps-script ops tests: ${passed} passed (offline mocks only)`);
