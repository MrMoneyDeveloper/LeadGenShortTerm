function sha256_(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
}

function namedFile_(folder, name) {
  const found = folder.getFilesByName(name);
  if (!found.hasNext()) return null;
  const file = found.next();
  if (found.hasNext()) throw new Error('BACKUP_DUPLICATE_NAME');
  if (file.isTrashed()) throw new Error('BACKUP_FILE_MISSING');
  return file;
}

function fileOnce_(folder, name, content, mime) {
  return namedFile_(folder, name) || folder.createFile(Utilities.newBlob(content, mime, name));
}

function checkedFileOnce_(folder, name, content, mime) {
  const file = fileOnce_(folder, name, content, mime);
  if (sha256_(file.getBlob().getDataAsString()) !== sha256_(content)) throw new Error('BACKUP_FILE_MISMATCH');
  return file;
}

function csvRows_(rows) {
  return rows.map(row => row.map(value => {
    // CSV opens outside Sheets too. Protect formulas while retaining the original JSON/text Sheet data.
    const text = String(safeCell_(String(value)));
    return '"' + text.replace(/"/g, '""') + '"';
  }).join(',')).join('\r\n') + '\r\n';
}

function saveState_(state) {
  const text = JSON.stringify(state);
  if (text.length > 8000) throw new Error('BACKUP_STATE_TOO_LARGE');
  config_().props.setProperty('BACKUP_STATE', text);
}

function backupSnapshot_() {
  return sheet_().getSheets().filter(tab => /^VALIDATED_\d{3,}$/.test(tab.getName()))
    .sort((a, b) => Number(a.getName().slice(10)) - Number(b.getName().slice(10)))
    .map(tab => ({tab: tab.getName(), through: tab.getLastRow(), columns: tab.getLastColumn()}))
    .filter(item => item.through >= 1 && item.columns >= 1);
}

function backupPart_(folder, state) {
  const snapshot = state.shards[state.shard];
  const count = Math.min(1000, snapshot.through - state.row + 1);
  const suffix = ('000000' + state.part).slice(-6);
  const name = 'validated-leads-' + state.date + '-' + snapshot.tab + '-part-' + suffix + '.csv';
  const savedMeta = namedFile_(folder, name + '.json');
  let meta;
  if (savedMeta) {
    meta = JSON.parse(savedMeta.getBlob().getDataAsString());
    if (meta.tab !== snapshot.tab || meta.row !== state.row || meta.through !== snapshot.through || meta.count !== count) {
      throw new Error('BACKUP_CHECKPOINT_MISMATCH');
    }
    const file = DriveApp.getFileById(meta.file_id);
    if (file.isTrashed() || sha256_(file.getBlob().getDataAsString()) !== meta.sha256) throw new Error('BACKUP_FILE_MISMATCH');
  } else {
    const tab = sheet_().getSheetByName(snapshot.tab);
    if (!tab || tab.getLastRow() < snapshot.through) throw new Error('BACKUP_SHEET_CHANGED');
    const header = tab.getRange(1, 1, 1, snapshot.columns).getDisplayValues();
    const rows = count > 0 ? tab.getRange(state.row, 1, count, snapshot.columns).getDisplayValues() : [];
    if (rows.some(row => !row[0])) throw new Error('BACKUP_SHEET_GAP');
    const content = csvRows_(header.concat(rows));
    const file = checkedFileOnce_(folder, name, content, 'text/csv');
    meta = {file: name, file_id: file.getId(), sha256: sha256_(content), tab: snapshot.tab,
      row: state.row, count: count, through: snapshot.through};
    checkedFileOnce_(folder, name + '.json', JSON.stringify(meta), 'application/json');
  }
  state.part++;
  state.row += count;
  if (state.row > snapshot.through) { state.shard++; state.row = 2; }
  if (state.shard >= state.shards.length) state.stage = 'stats';
  saveState_(state);
}

function snapshotApiFile_(folder, name, path, mime) {
  // Reuse an earlier snapshot after a crash; operational statistics can have moved since then.
  const file = namedFile_(folder, name) || checkedFileOnce_(folder, name, api_(path).getContentText(), mime);
  return {file: name, file_id: file.getId(), sha256: sha256_(file.getBlob().getDataAsString())};
}

function completeBackup_(state, manifest) {
  // History precedes the completion checkpoint, and is repaired from the manifest after a crash.
  exportHistory_(state, 'SUCCESS', manifest.getUrl());
  saveState_(state);
  status_('BACKUP', 'SUCCESS', state.date + '; parts=' + (state.part - 1));
}

/** Bounded final-Sheet snapshot; acknowledged leads may already be deleted from Postgres. */
function dailyBackup() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  const started = Date.now();
  let state;
  try {
    const cfg = config_();
    if (!cfg.folder) throw new Error('CONFIG_DRIVE_FOLDER');
    const folder = DriveApp.getFolderById(cfg.folder);
    const today = Utilities.formatDate(new Date(), 'Africa/Johannesburg', 'yyyy-MM-dd');
    state = JSON.parse(cfg.props.getProperty('BACKUP_STATE') || 'null');
    if (!state || state.complete) {
      const prior = namedFile_(folder, 'backup-manifest-' + today + '.json');
      if (prior) {
        const old = JSON.parse(prior.getBlob().getDataAsString());
        if (!old.complete || old.spreadsheet_id !== cfg.sheet) throw new Error('BACKUP_MANIFEST_MISMATCH');
        completeBackup_(old, prior);
        return;
      }
      const shards = backupSnapshot_();
      state = {date: today, spreadsheet_id: cfg.sheet, shards: shards, shard: 0, row: 2, part: 1,
        stage: shards.length ? 'leads' : 'stats', complete: false, started_at: new Date().toISOString()};
      saveState_(state);
    }
    if (state.spreadsheet_id !== cfg.sheet) throw new Error('BACKUP_DESTINATION_MISMATCH');
    while (state.stage === 'leads' && Date.now() - started < 180000) backupPart_(folder, state);
    if (state.stage === 'stats' && Date.now() - started < 180000) {
      const files = [snapshotApiFile_(folder, 'source-stats-' + state.date + '.csv', '/exports/source-stats', 'text/csv'),
        snapshotApiFile_(folder, 'processing-summary-' + state.date + '.json', '/exports/processing-summary', 'application/json')];
      state.stage = 'complete';
      state.complete = true;
      state.completed_at = new Date().toISOString();
      state.statistics_files = files;
      // CSV sidecars carry per-part checksums; this manifest pins the shard snapshot and part count.
      const manifestName = 'backup-manifest-' + state.date + '.json';
      const manifest = namedFile_(folder, manifestName) || checkedFileOnce_(folder, manifestName, JSON.stringify(state), 'application/json');
      const saved = JSON.parse(manifest.getBlob().getDataAsString());
      if (!saved.complete || saved.spreadsheet_id !== cfg.sheet || saved.date !== state.date) throw new Error('BACKUP_MANIFEST_MISMATCH');
      completeBackup_(saved, manifest);
    } else {
      status_('BACKUP', 'IN_PROGRESS', state.date + '; next part=' + state.part);
      exportHistory_(state, 'IN_PROGRESS', '');
    }
  } catch (e) {
    status_('BACKUP', 'FAILED', errorCode_(e));
    try {
      writeRows_('FAILED', [{component: 'backup', code: errorCode_(e), at: new Date().toISOString()}]);
      if (state) exportHistory_(state, 'FAILED', '');
    } catch (ignored) {}
    throw new Error(errorCode_(e));
  } finally { lock.releaseLock(); }
}
