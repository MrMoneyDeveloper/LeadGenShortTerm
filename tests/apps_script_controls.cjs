/** Focused, offline tests for bounded scheduled delivery. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function fixture(expiry) {
  const values = new Map([['DELIVERY_ENABLED', 'true']]);
  if (expiry) values.set('DELIVERY_EXPIRES_AT', expiry);
  const removed = [], calls = [];
  const delivery = {getHandlerFunction: () => 'deliveryTick'};
  const other = {getHandlerFunction: () => 'dashboardTick'};
  const props = {getProperty: k => values.get(k) || null, setProperty: (k,v) => values.set(k,v)};
  const context = vm.createContext({Date, config_: () => ({props}),
    ScriptApp: {getProjectTriggers: () => [delivery, other], deleteTrigger: t => removed.push(t)},
    status_: (_component,status) => props.setProperty('STATUS_DELIVERY', JSON.stringify({status})),
    errorCode_: () => 'GOOGLE_OPERATION_FAILED',
    deliverFinalLeads: () => {calls.push('delivery'); props.setProperty('STATUS_DELIVERY', '{"status":"IDLE"}');}});
  vm.runInContext(fs.readFileSync('apps-script/Controls.gs','utf8'), context);
  return {context,values,removed,calls,delivery};
}
let e = fixture('2000-01-01T00:00:00Z'); e.context.deliveryTick();
assert.equal(e.calls.length,0); assert.equal(e.values.get('DELIVERY_ENABLED'),'false');
assert.deepEqual(e.removed,[e.delivery]);
e = fixture('invalid'); assert.throws(() => e.context.deliveryTick(), /CONFIG_DELIVERY_EXPIRATION/);
assert.equal(e.calls.length,0);
e = fixture('2999-01-01T00:00:00Z');
for(let i=0;i<12;i++) e.context.deliveryTick();
const records = JSON.parse(e.values.get('DELIVERY_TEST_EVENTS'));
assert.equal(records.length,10); assert.ok(records.every(r => r.status === 'IDLE' && Number.isFinite(Date.parse(r.at))));
assert.equal(e.removed.length,0);
e = fixture(); e.context.deliveryTick(); assert.equal(e.calls.length,1);
assert.equal(e.values.has('DELIVERY_TEST_EVENTS'),false);
console.log('4 bounded delivery checks passed; zero external calls.');
