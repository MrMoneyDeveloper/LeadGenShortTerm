const crypto = require('crypto');
const fs = require('fs');
const vm = require('vm');

global.Utilities = {
  DigestAlgorithm: {SHA_256: 'sha256'},
  Charset: {UTF_8: 'utf8'},
  computeDigest: (_algorithm, text) => Array.from(crypto.createHash('sha256').update(text, 'utf8').digest()).map(x => x > 127 ? x - 256 : x),
  newBlob: (content, mime, name) => ({content, mime, name})
};
vm.runInThisContext(fs.readFileSync('apps-script/Backups.gs', 'utf8'));

function assert(value, message) { if (!value) throw new Error(message); }
assert(sha256_('abc') === 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad', 'sha256 mismatch');

const state = {date: '2026-09-13', through: 9, after: 0, part: 1, stage: 'leads'};
const next = advanceBackupState_(state, {through: 9, after: 0, next_after: 4});
assert(next.after === 4 && next.part === 2 && next.stage === 'leads', 'resume cursor mismatch');
const done = advanceBackupState_(next, {through: 9, after: 4, next_after: null});
assert(done.stage === 'stats' && done.part === 3, 'terminal page mismatch');
assert(state.after === 0 && state.part === 1, 'state mutated in place');
let failed = false;
try { validatePartMeta_({through: 10, after: 0, next_after: 4}, state); } catch (e) { failed = e.message === 'BACKUP_CHECKPOINT_MISMATCH'; }
assert(failed, 'snapshot mismatch accepted');

function iterator(items) { return {hasNext: () => items.length > 0, next: () => items.shift()}; }
const files = [];
const folder = {
  getFilesByName: name => iterator(files.filter(f => f.name === name).slice()),
  createFile: blob => { const file = {name: blob.name, content: blob.content}; files.push(file); return file; }
};
const one = fileOnce_(folder, 'part.csv', 'a,b', 'text/csv');
const two = fileOnce_(folder, 'part.csv', 'changed', 'text/csv');
assert(one === two && files.length === 1 && one.content === 'a,b', 'create-once failed');
console.log('apps-script-backup-tests: pass');
