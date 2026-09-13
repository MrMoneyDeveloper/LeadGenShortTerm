/** Final output is append-only. Render owns placement and a durable immutable batch. */
function validateDelivery_(batch) {
  const cfg = config_();
  if (!batch || batch.spreadsheet_id !== cfg.sheet || batch.drive_folder_id !== cfg.folder) {
    throw new Error('DELIVERY_DESTINATION_MISMATCH');
  }
  if (!/^[a-f0-9]{64}$/.test(batch.checksum || '') || !batch.batch_id || !Array.isArray(batch.fields) ||
      !batch.fields.length || batch.fields[0] !== 'lead_id' || !Array.isArray(batch.items) ||
      batch.items.length < 1 || batch.items.length > 200 || sha256_(JSON.stringify(batch.items)) !== batch.checksum) {
    throw new Error('DELIVERY_BATCH_INVALID');
  }
  const ids = {}, places = {};
  batch.items.forEach(item => {
    const place = item.tab + ':' + item.row;
    if (!Number.isSafeInteger(item.lead_id) || item.lead_id < 1 ||
        !/^VALIDATED_\d{3,}$/.test(item.tab) || !Number.isInteger(item.row) || item.row < 2 || item.row > 5001 ||
        !Array.isArray(item.values) || item.values.length !== batch.fields.length ||
        item.values.some(v => typeof v !== 'string') ||
        item.values[0] !== String(batch.campaign_id) + ':' + item.lead_id || ids[item.lead_id] || places[place]) {
      throw new Error('DELIVERY_PLACEMENT_INVALID');
    }
    ids[item.lead_id] = true;
    places[place] = true;
  });
  return batch;
}

function sameStrings_(left, right) {
  return left.length === right.length && left.every((value, i) => String(value) === right[i]);
}

function ensureGrid_(tab, rows, columns) {
  if (tab.getMaxRows() < rows) tab.insertRowsAfter(tab.getMaxRows(), rows - tab.getMaxRows());
  if (tab.getMaxColumns() < columns) tab.insertColumnsAfter(tab.getMaxColumns(), columns - tab.getMaxColumns());
}

/** Rich text writes preserve literal text (including =,+,-,@), without formula execution. */
function writeLiteralRows_(range, rows) {
  range.setRichTextValues(rows.map(row => row.map(value => SpreadsheetApp.newRichTextValue().setText(value).build())));
}

function deliveryGroups_(batch) {
  const groups = [];
  batch.items.slice().sort((a, b) => a.tab.localeCompare(b.tab) || a.row - b.row).forEach(item => {
    const previous = groups[groups.length - 1];
    if (previous && previous.tab === item.tab && previous.row + previous.items.length === item.row) previous.items.push(item);
    else groups.push({tab: item.tab, row: item.row, items: [item]});
  });
  return groups;
}

function verifyDeliverySheets_(batch, allowWrite) {
  const book = sheet_(), groups = deliveryGroups_(batch), plans = [], headers = {};
  groups.forEach(group => {
    let tab = book.getSheetByName(group.tab);
    if (!tab) {
      if (!allowWrite) throw new Error('DELIVERY_SHEET_MISSING');
      tab = book.insertSheet(group.tab);
    }
    ensureGrid_(tab, group.row + group.items.length - 1, batch.fields.length);
    const header = tab.getRange(1, 1, 1, batch.fields.length).getDisplayValues()[0];
    const emptyHeader = header.every(v => v === '');
    if (!emptyHeader && !sameStrings_(header, batch.fields)) throw new Error('DELIVERY_HEADER_CONFLICT');
    if (emptyHeader && !allowWrite) throw new Error('DELIVERY_HEADER_CONFLICT');
    headers[group.tab] = {tab: tab, empty: emptyHeader};
    const range = tab.getRange(group.row, 1, group.items.length, batch.fields.length);
    const old = range.getDisplayValues(), formulas = range.getFormulas();
    group.items.forEach((item, index) => {
      if (formulas[index].some(v => v !== '') ||
          (!old[index].every(v => v === '') && !sameStrings_(old[index], item.values))) {
        throw new Error('DELIVERY_ROW_CONFLICT');
      }
      if (!allowWrite && !sameStrings_(old[index], item.values)) throw new Error('DELIVERY_READBACK_FAILED');
    });
    plans.push({range: range, values: group.items.map(item => item.values)});
  });
  // Inspect the whole batch before writing any existing row. Exact retries are safe.
  if (allowWrite) {
    Object.keys(headers).forEach(name => {
      const entry = headers[name];
      if (entry.empty) writeLiteralRows_(entry.tab.getRange(1, 1, 1, batch.fields.length), [batch.fields]);
      entry.tab.setFrozenRows(1);
    });
    plans.forEach(plan => writeLiteralRows_(plan.range, plan.values));
    SpreadsheetApp.flush();
    verifyDeliverySheets_(batch, false);
  }
}

function readDeliveryFile_(file, expectedChecksum) {
  if (file.isTrashed()) throw new Error('DELIVERY_FILE_MISSING');
  const batch = validateDelivery_(JSON.parse(file.getBlob().getDataAsString()));
  if (batch.checksum !== expectedChecksum) throw new Error('DELIVERY_FILE_MISMATCH');
  return batch;
}

function deliveryEnvelope_(batch) {
  return {batch_id: batch.batch_id, checksum: batch.checksum, campaign_id: batch.campaign_id,
    spreadsheet_id: batch.spreadsheet_id, drive_folder_id: batch.drive_folder_id,
    fields: batch.fields, items: batch.items};
}

function acknowledgeDelivery_(batch, file) {
  const result = JSON.parse(api_('/exports/ack', 'post', {batch_id: batch.batch_id, checksum: batch.checksum,
    spreadsheet_id: batch.spreadsheet_id, drive_folder_id: batch.drive_folder_id,
    drive_file_id: file.getId(), drive_checksum: sha256_(JSON.stringify(batch.items)),
    placements: batch.items.map(item => ({lead_id: item.lead_id, tab: item.tab, row: item.row}))}, true).getContentText());
  if (result.status !== 'ACKNOWLEDGED') throw new Error('DELIVERY_ACK_INVALID');
  deliveryIndex_(batch, 'ACKNOWLEDGED', file.getUrl());
  config_().props.deleteProperty('DELIVERY_STATE');
  status_('DELIVERY', 'SUCCESS', 'batch=' + batch.batch_id + '; rows=' + batch.items.length);
}

/** One batch only, <=200 final leads. No collection, classification or scheduling here. */
function deliverFinalLeads() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    const cfg = config_();
    if (!cfg.sheet || !cfg.folder) throw new Error('CONFIG_DELIVERY_DESTINATIONS');
    const saved = JSON.parse(cfg.props.getProperty('DELIVERY_STATE') || 'null');
    if (saved) {
      const priorFile = DriveApp.getFileById(saved.file_id);
      const prior = readDeliveryFile_(priorFile, saved.checksum);
      if (prior.batch_id !== saved.batch_id) throw new Error('DELIVERY_FILE_MISMATCH');
      verifyDeliverySheets_(prior, false);
      acknowledgeDelivery_(prior, priorFile);
      return;
    }
    const batch = JSON.parse(api_('/exports/claim', 'post', {limit: 200}, true).getContentText());
    if (batch.status === 'empty' && Array.isArray(batch.items) && !batch.items.length) {
      status_('DELIVERY', 'IDLE', 'No final leads awaiting delivery.');
      return;
    }
    validateDelivery_(batch);
    verifyDeliverySheets_(batch, true);
    const folder = DriveApp.getFolderById(cfg.folder);
    // Hash the ID for a filename with no path/control characters; the original ID is in the envelope.
    const stem = 'lead-delivery-' + sha256_(String(batch.batch_id));
    const envelope = JSON.stringify(deliveryEnvelope_(batch));
    const file = checkedFileOnce_(folder, stem + '.json', envelope, 'application/json');
    readDeliveryFile_(file, batch.checksum);
    checkedFileOnce_(folder, stem + '.csv', csvRows_([batch.fields].concat(batch.items.map(item => item.values))), 'text/csv');
    // Only references enter Script Properties. A retry after a lost ACK response resends the same ACK.
    cfg.props.setProperty('DELIVERY_STATE', JSON.stringify({batch_id: batch.batch_id, checksum: batch.checksum, file_id: file.getId()}));
    deliveryIndex_(batch, 'WRITTEN', file.getUrl());
    acknowledgeDelivery_(batch, file);
  } catch (e) {
    status_('DELIVERY', 'FAILED', errorCode_(e));
    try { writeRows_('FAILED', [{component: 'delivery', code: errorCode_(e), at: new Date().toISOString()}]); } catch (ignored) {}
    throw new Error(errorCode_(e));
  } finally { lock.releaseLock(); }
}
