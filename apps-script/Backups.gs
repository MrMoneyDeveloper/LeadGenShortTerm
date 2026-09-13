function sha256_(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
}

function fileOnce_(folder, name, content, mime) {
  const existing = folder.getFilesByName(name);
  if (existing.hasNext()) {
    const file = existing.next();
    if (existing.hasNext()) throw new Error('BACKUP_DUPLICATE_NAME');
    return file;
  }
  return folder.createFile(Utilities.newBlob(content, mime, name));
}

function saveState_(state) {
  config_().props.setProperty('BACKUP_STATE', JSON.stringify(state));
}

function validatePartMeta_(meta, state) {
  if (!meta || meta.through !== state.through || meta.after !== state.after) throw new Error('BACKUP_CHECKPOINT_MISMATCH');
  if (meta.next_after !== null && (!Number.isFinite(meta.next_after) || meta.next_after <= state.after)) {
    throw new Error('BACKUP_PAGINATION_INVALID');
  }
  return meta;
}

function advanceBackupState_(state, meta) {
  validatePartMeta_(meta, state);
  const next = Object.assign({}, state, {part: state.part + 1});
  if (meta.next_after === null) next.stage = 'stats';
  else next.after = meta.next_after;
  return next;
}

/** Bounded, resumable full useful-lead snapshot. Never fetches source_records. */
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
    if (state && state.complete && state.date === today) return;
    if (!state || state.complete) {
      const prior = folder.getFilesByName('backup-manifest-' + today + '.json');
      if (prior.hasNext()) {
        const old = JSON.parse(prior.next().getBlob().getDataAsString());
        if (old.complete) {
          saveState_(old);
          status_('BACKUP', 'SUCCESS', today);
          return;
        }
      }
      const page = json_('/exports/validated?format=json&limit=1');
      state = {date: today, through: page.through, after: 0, part: 1,
        stage: 'leads', complete: false, started_at: new Date().toISOString()};
      saveState_(state);
    }
    while (state.stage === 'leads' && Date.now() - started < 180000) {
      const suffix = ('000000' + state.part).slice(-6);
      const name = 'validated-leads-' + state.date + '-part-' + suffix + '.csv';
      const metaName = name + '.json';
      const existingMeta = folder.getFilesByName(metaName);
      let meta;
      if (existingMeta.hasNext()) {
        meta = JSON.parse(existingMeta.next().getBlob().getDataAsString());
        validatePartMeta_(meta, state);
        const saved = DriveApp.getFileById(meta.file_id);
        if (saved.isTrashed() || sha256_(saved.getBlob().getDataAsString()) !== meta.sha256) throw new Error('BACKUP_FILE_MISMATCH');
      } else {
        const response = api_('/exports/validated?format=csv&limit=1000&after=' + state.after + '&through=' + state.through);
        const content = response.getContentText();
        const headers = response.getAllHeaders();
        const lower = {};
        Object.keys(headers).forEach(k => lower[k.toLowerCase()] = headers[k]);
        if (Number(lower['x-export-through']) !== state.through) throw new Error('BACKUP_SNAPSHOT_MISMATCH');
        if (!Object.prototype.hasOwnProperty.call(lower, 'x-next-after')) throw new Error('BACKUP_PAGINATION_MISSING');
        const next = lower['x-next-after'] === '' ? null : Number(lower['x-next-after']);
        validatePartMeta_({through: state.through, after: state.after, next_after: next}, state);
        const file = fileOnce_(folder, name, content, 'text/csv');
        if (sha256_(file.getBlob().getDataAsString()) !== sha256_(content)) throw new Error('BACKUP_FILE_MISMATCH');
        meta = {file: name, file_id: file.getId(), sha256: sha256_(content), after: state.after,
          through: state.through, next_after: next};
        fileOnce_(folder, metaName, JSON.stringify(meta, null, 2), 'application/json');
      }
      state = advanceBackupState_(state, meta);
      saveState_(state);
    }
    if (state.stage === 'stats' && Date.now() - started < 180000) {
      fileOnce_(folder, 'source-stats-' + state.date + '.csv', api_('/exports/source-stats').getContentText(), 'text/csv');
      fileOnce_(folder, 'processing-summary-' + state.date + '.json', api_('/exports/processing-summary').getContentText(), 'application/json');
      state.stage = 'complete';
      state.complete = true;
      state.completed_at = new Date().toISOString();
      const manifest = fileOnce_(folder, 'backup-manifest-' + state.date + '.json', JSON.stringify(state, null, 2), 'application/json');
      // Write dashboard history before checkpoint complete; failures retry safely.
      exportHistory_(state, 'SUCCESS', manifest.getUrl());
      saveState_(state);
      status_('BACKUP', 'SUCCESS', state.date + '; parts=' + (state.part - 1));
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
