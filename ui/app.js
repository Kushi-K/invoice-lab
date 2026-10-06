// Navigation, document review, and transparent local model requests.
const $ = selector => document.querySelector(selector);
const state = {
  document: null, response: null, history: [], historyPage: 1,
  section: 'fields', page: 1, sourceOpen: false, selected: null,
  drafts: new Map(), note: '', confirmAll: false, saving: false,
  models: [], model: '', modelStatus: 'checking', modelMessage: '', modelAction: '',
  modelBusy: null, modelFailure: null, preview: null, previewError: null,
  job: null, jobTimer: null, jobRun: null, requestStage: 'extract', codeTab: 'prompt', previewSequence: 0, routeSequence: 0
};
const fieldLabels = {
  supplier_name: 'Supplier name', supplier_gstin: 'Supplier GSTIN',
  buyer_name: 'Buyer name', buyer_gstin: 'Buyer GSTIN',
  invoice_number: 'Invoice number', invoice_date: 'Invoice date',
  taxable_amount: 'Taxable amount', cgst: 'CGST', sgst: 'SGST', igst: 'IGST',
  grand_total: 'Grand total', carrier: 'Carrier', order_number: 'Order number',
  invoice_references: 'Invoice references', total_carrier_pay: 'Total carrier pay'
};
const sections = [['fields', 'Fields'], ['items', 'Line items'], ['structure', 'How extraction works'], ['model', 'AI explanation & results'], ['evidence', 'OCR transcript']];
const allowed = /\.(pdf|png|jpe?g|webp|tiff?)$/i;
const make = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
const empty = node => node.replaceChildren();
const badge = (text, type = '') => make('span', `pill ${type}`, text);
const score = value => value == null ? '—' : `${Math.round(Number(value) * 100)}`;
const ocrScore = value => value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;
const pretty = value => JSON.stringify(value, null, 2);
const keyFor = edit => JSON.stringify(edit);
const report = () => state.response?.report;

function action(text, callback, className = 'button secondary') {
  const button = make('button', className, text);
  button.type = 'button'; button.addEventListener('click', callback); return button;
}
function notify(message, error = false) {
  const node = $('#notification'); node.textContent = message;
  node.className = error ? 'notice error' : 'notice'; node.hidden = false;
}
async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {const error = new Error(data.error || `Request failed (${response.status}).`); error.status = response.status; throw error;}
  return data;
}
const post = (path, data) => api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
function download(data, name) {
  const url = URL.createObjectURL(new Blob([pretty(data)], {type: 'application/json'}));
  const link = make('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function panel(title, description) {
  const node = make('section', 'panel'); const heading = make('div', 'panel-title');
  heading.append(make('h2', '', title)); node.append(heading);
  if (description) node.append(make('p', 'panel-description', description));
  return node;
}
function showView(view) {
  for (const name of ['new', 'results', 'analysis']) $(`#view-${name}`).hidden = name !== view;
  $('#nav-new').classList.toggle('active', view === 'new');
  $('#nav-results').classList.toggle('active', view !== 'new');
  $('#breadcrumb').textContent = `Workspace / ${view === 'new' ? 'New analysis' : view === 'results' ? 'Saved results' : 'Document analysis'}`;
}
async function route() {
  const sequence = ++state.routeSequence;
  window.scrollTo(0, 0);
  const match = /^#\/analysis\/([0-9a-f]{32})(?:\/(fields|items|structure|model|evidence))?$/.exec(location.hash);
  $('#notification').hidden = true;
  if (match) {
    const runId = match[1];
    if (state.response?.run_id !== runId) {
      if (state.drafts.size && !window.confirm('Discard unsaved edits before opening another document?')) {
        location.hash = `#/analysis/${state.response.run_id}/${state.section}`; return;
      }
      showView('analysis'); $('#analysis-name').textContent = 'Loading analysis…';
      $('#analysis-content').replaceChildren(make('p', 'route-loading', 'Loading saved evidence and values…'));
      try {
        const saved = await api(`/api/report/${runId}`);
        if (sequence !== state.routeSequence) return;
        state.response = saved; state.drafts.clear(); state.note = ''; state.confirmAll = false;
        state.page = saved.report.rate_confirmations?.[0]?.page || 1;
        state.selected = null; state.preview = null; state.previewSequence++; state.modelFailure = null;
        if (saved.report.llm_model && state.models.includes(saved.report.llm_model)) state.model = saved.report.llm_model;
      } catch (error) { notify(error.message, true); return; }
    }
    state.section = match[2] || 'fields'; showView('analysis'); renderAnalysis();
    if (state.section === 'structure') loadPreview();
    if (state.jobRun !== runId) monitorJob(runId);
  } else if (location.hash === '#/results') {
    showView('results'); renderHistory(); await refreshHistory();
  } else {
    showView('new');
  }
}
window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', event => {
  if (state.drafts.size) {event.preventDefault(); event.returnValue = '';}
});

// Upload is its own view; analysis opens independently of saved-result navigation.
function choose(file) {
  if (!file) return;
  if (!allowed.test(file.name)) return notify('Choose a PDF, PNG, JPG, WebP or TIFF file.', true);
  if (file.size > 25 * 1024 * 1024) return notify('Choose a file smaller than 25 MB.', true);
  state.document = file; $('#notification').hidden = true;
  $('#file-selected').hidden = false;
  $('#file-selected').textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
}
$('#dropzone').addEventListener('click', () => $('#document').click());
$('#dropzone').addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); $('#document').click();}
});
$('#document').addEventListener('change', event => choose(event.target.files[0]));
for (const name of ['dragenter', 'dragover', 'dragleave', 'drop']) {
  $('#dropzone').addEventListener(name, event => {
    event.preventDefault(); $('#dropzone').classList.toggle('drag-over', ['dragenter', 'dragover'].includes(name));
    if (name === 'drop') choose(event.dataTransfer.files[0]);
  });
}
$('#extract-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.document) return notify('Choose a document first.', true);
  if (state.drafts.size && !window.confirm('Discard unsaved edits before analyzing a new document?')) return;
  const form = new FormData(); form.append('document', state.document);
  form.append('force_ocr', String($('#force-ocr').checked));
  if ($('#truth').files[0]) form.append('truth', $('#truth').files[0]);
  $('#run-button').disabled = true; $('#upload-progress').hidden = false; $('#notification').hidden = true;
  try {
    const saved = await api('/api/extract', {method: 'POST', body: form});
    state.response = saved; state.drafts.clear(); state.note = ''; state.confirmAll = false;
    state.page = saved.report.rate_confirmations?.[0]?.page || 1; state.selected = null; state.preview = null;
    location.hash = `#/analysis/${saved.run_id}/fields`; refreshHistory();
    if ($('#auto-ai').checked && state.modelStatus === 'ready' && state.model) await runModel('analyze');
  } catch (error) { notify(error.message, true); }
  finally {$('#run-button').disabled = false; $('#upload-progress').hidden = true;}
});

// Searchable, paginated history with actual human-review progress.
async function refreshHistory() {
  try {
    const data = await api('/api/history'); state.history = data.results;
    $('#history-count').textContent = state.history.length; renderHistory();
  } catch (error) { notify(error.message, true); }
}
function renderHistory() {
  const query = $('#history-search').value.toLowerCase(); const filter = $('#history-filter').value;
  const rows = state.history.filter(entry => entry.source.toLowerCase().includes(query) && (filter === 'all' || entry.review_status === filter));
  const pages = Math.max(1, Math.ceil(rows.length / 10)); state.historyPage = Math.min(state.historyPage, pages);
  const start = (state.historyPage - 1) * 10; const container = $('#history-table'); empty(container);
  if (!rows.length) {
    const node = make('div', 'empty-state'); node.append(make('h3', '', query || filter !== 'all' ? 'No matching documents' : 'No analyses yet'), make('p', '', 'Upload a document to create your first analysis, or adjust your search.')); container.append(node);
  } else {
    const table = make('table', 'data-table'); const head = table.createTHead().insertRow();
    for (const [label, className] of [['Document', ''], ['Pages', 'history-pages'], ['Review', ''], ['Model', 'history-model'], ['Updated', 'history-date'], ['', '']]) head.append(make('th', className, label));
    const body = table.createTBody();
    for (const entry of rows.slice(start, start + 10)) {
      const row = body.insertRow(); const doc = make('div', 'document-cell');
      doc.append(make('span', 'file-icon', /\.pdf$/i.test(entry.source) ? 'PDF' : 'IMG'));
      const name = make('div'); name.append(make('span', 'document-name', entry.source), make('small', '', entry.document_type === 'rate_confirmation_packet' ? 'Rate confirmation' : 'Invoice')); doc.append(name); row.insertCell().append(doc);
      row.insertCell().className = 'history-pages'; row.cells[1].textContent = entry.pages;
      const review = row.insertCell(); const status = entry.review_status || 'unreviewed';
      review.append(badge(({reviewed: 'Reviewed', in_progress: 'In progress', unreviewed: 'Not reviewed'})[status], status === 'reviewed' ? 'success' : status === 'in_progress' ? 'info' : ''));
      const model = row.insertCell(); model.className = 'history-model'; model.textContent = ({reviewed: 'Review complete', extracted: 'Extracted', not_run: 'Not run'})[entry.model_status] || 'Not run';
      const updated = row.insertCell(); updated.className = 'history-date'; updated.textContent = new Date(entry.updated_at).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'}); updated.append(make('small', 'row-caption', new Date(entry.updated_at).toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'})));
      const link = make('a', 'text-link', 'Open →'); link.href = `#/analysis/${entry.run_id}/fields`; link.setAttribute('aria-label', `Open ${entry.source}`); row.insertCell().append(link);
    }
    container.append(table);
  }
  $('#history-summary').textContent = rows.length ? `${start + 1}–${Math.min(start + 10, rows.length)} of ${rows.length} documents` : '0 documents';
  $('#history-page').textContent = `${state.historyPage} / ${pages}`;
  $('#history-prev').disabled = state.historyPage === 1; $('#history-next').disabled = state.historyPage === pages;
}
$('#history-search').addEventListener('input', () => {state.historyPage = 1; renderHistory();});
$('#history-filter').addEventListener('change', () => {state.historyPage = 1; renderHistory();});
$('#refresh-history').addEventListener('click', refreshHistory);
$('#history-prev').addEventListener('click', () => {state.historyPage--; renderHistory();});
$('#history-next').addEventListener('click', () => {state.historyPage++; renderHistory();});

function entries(includeItems = false) {
  const r = report(); const result = [];
  if (r.document_type === 'rate_confirmation_packet') {
    for (const record of r.rate_confirmations || []) {
      for (const field of ['carrier', 'order_number', 'total_carrier_pay']) result.push({label: fieldLabels[field], field: record[field], group: `Form page ${record.page}`, edit: {kind: 'rate', page: record.page, field}});
      for (const [index, field] of record.invoice_references.entries()) result.push({label: `Invoice reference ${index + 1}`, field, group: `Form page ${record.page}`, edit: {kind: 'rate', page: record.page, field: 'invoice_references', index}});
    }
  } else {
    for (const [field, value] of Object.entries(r.fields || {})) {
      const group = field.startsWith('supplier') ? 'Supplier' : field.startsWith('buyer') ? 'Buyer' : field.startsWith('invoice') ? 'Invoice details' : 'Amounts & taxes';
      result.push({label: fieldLabels[field] || field, field: value, group, edit: {kind: 'invoice', field}});
    }
  }
  if (includeItems) for (const [index, item] of (r.items || []).entries()) for (const field of ['description', 'quantity', 'rate', 'amount']) {
    result.push({label: `Item ${index + 1} ${field}`, field: {value: item[field], ...item.human_reviews?.[field]}, edit: {kind: 'item', index, field}, group: 'Line items'});
  }
  return result;
}
function renderAnalysis() {
  const r = report(); if (!r) return;
  $('#analysis-name').textContent = r.source;
  const meta = $('#analysis-meta'); empty(meta);
  meta.append(badge(r.document_type === 'rate_confirmation_packet' ? 'Rate confirmation' : 'Invoice', 'info'), make('span', '', `${r.pages.length} page${r.pages.length === 1 ? '' : 's'}`), make('span', '', [...new Set(r.pages.map(p => p.method === 'pdf_text' ? 'Embedded PDF text' : 'Tesseract OCR'))].join(' + ')));
  const summary = $('#analysis-summary'); empty(summary);
  const fieldEntries = entries();
  const stats = [[`${fieldEntries.filter(e => e.field.value != null).length}/${fieldEntries.length}`, 'fields populated'], [fieldEntries.filter(e => e.field.review_status?.startsWith('human_')).length, 'human reviewed'], [r.totals_validation?.status || 'Not checked', 'totals check']];
  for (const [value, label] of stats) {const span = make('span'); span.append(make('b', '', value), document.createTextNode(label)); summary.append(span);}
  const tabs = $('#analysis-tabs'); empty(tabs);
  for (const [key, title] of sections) {
    if (key === 'items' && r.document_type === 'rate_confirmation_packet') continue;
    const link = make('a', state.section === key ? 'active' : '', title); link.href = `#/analysis/${state.response.run_id}/${key}`;
    if (state.section === key) link.setAttribute('aria-current', 'page'); tabs.append(link);
  }
  const content = $('#analysis-content'); empty(content);
  if (state.section === 'fields') renderFields(content);
  else if (state.section === 'items') renderItems(content);
  else if (state.section === 'structure') renderStructure(content);
  else if (state.section === 'model') renderModelResults(content);
  else renderEvidence(content);
  $('#save-bar').hidden = !['fields', 'items'].includes(state.section);
  $('#confirm-label').textContent = state.section === 'items' ? 'Confirm all item values' : 'Confirm all populated fields';
  $('#review-note').value = state.note; $('#confirm-values').checked = state.confirmAll;
  updateSaveBar(); renderSource(); renderJobBanner();
  $('#run-ai').disabled = state.modelStatus !== 'ready' || !state.model || Boolean(state.modelBusy) || Boolean(state.drafts.size);
  $('#run-ai').textContent = state.modelBusy ? 'AI analysis running…' : 'Run AI analysis';
}
function valueInput(entry) {
  const key = keyFor(entry.edit); const input = make('input', 'value-input');
  input.type = 'text'; input.maxLength = 200; input.value = state.drafts.get(key)?.value ?? entry.field.value ?? '';
  input.placeholder = 'Not extracted'; input.setAttribute('aria-label', entry.label); input.dataset.editKey = key;
  input.classList.toggle('dirty', state.drafts.has(key)); input.classList.toggle('missing', entry.field.value == null);
  input.disabled = state.saving;
  input.addEventListener('input', () => {
    if (input.value === String(entry.field.value ?? '')) state.drafts.delete(key);
    else state.drafts.set(key, {...entry.edit, value: input.value});
    input.classList.toggle('dirty', state.drafts.has(key)); input.closest('.editable-value')?.classList.toggle('editing', true); updateSaveBar();
  });
  input.addEventListener('focus', () => input.closest('.editable-value')?.classList.add('editing'));
  input.addEventListener('blur', () => input.closest('.editable-value')?.classList.remove('editing'));
  const wrap = make('div', 'editable-value'); const pencil = action('✎', () => {input.focus(); input.select();}, 'edit-value-button'); pencil.setAttribute('aria-label', `Edit ${entry.label}`);
  wrap.append(input, pencil); return wrap;
}
function fieldState(field) {
  if (field.review_status === 'human_verified') return ['Verified', 'success'];
  if (field.review_status === 'human_corrected') return ['Corrected', 'info'];
  if (field.value == null) return ['Missing', 'warning'];
  if (field.review_status === 'needs_review' || field.review_priority === 'high') return ['Needs review', 'warning'];
  return [field.extraction_provider === 'ollama' ? 'Model extracted' : 'Not reviewed', ''];
}
function renderFields(content) {
  const node = panel('Extracted fields', 'Click inside a value to type a correction, or use its pencil button. Changed values are highlighted. Save all corrections with the button at the bottom.');
  const table = make('table', 'field-table'); const head = table.createTHead().insertRow();
  for (const [title, className] of [['Field', 'field-label'], ['Value · editable', 'value-column'], ['OCR', 'score-column'], ['Evidence /100', 'score-column'], ['Source', 'source-column']]) head.append(make('th', className, title));
  const body = table.createTBody(); let previousGroup = null;
  for (const entry of entries()) {
    if (entry.group !== previousGroup) {const row = body.insertRow(); row.className = 'group-row'; const cell = row.insertCell(); cell.colSpan = 5; cell.textContent = entry.group; previousGroup = entry.group;}
    const row = body.insertRow(); const label = row.insertCell(); label.className = 'field-label'; label.append(make('span', '', entry.label));
    const [status] = fieldState(entry.field); label.append(make('small', 'row-caption', status));
    label.append(make('small', 'row-caption mobile-scores', `OCR ${ocrScore(entry.field.ocr_confidence)} · Evidence ${score(entry.field.triage_score)}/100`));
    const value = row.insertCell(); value.className = 'value-column'; value.append(valueInput(entry));
    if (Object.hasOwn(entry.field, 'original_value') && entry.field.original_value !== entry.field.value) value.append(make('small', 'row-caption', `Original: ${entry.field.original_value ?? 'not extracted'}`));
    const ocr = row.insertCell(); ocr.className = 'score-column'; ocr.textContent = ocrScore(entry.field.ocr_confidence);
    const confidence = row.insertCell(); confidence.className = 'score-column'; confidence.textContent = score(entry.field.triage_score);
    const source = row.insertCell(); source.className = 'source-column'; const button = action('⌖', () => inspectSource(entry.field, entry.label), 'source-button'); button.setAttribute('aria-label', `Source for ${entry.label}`); source.append(button);
  }
  node.append(table); content.append(node, make('p', 'scores-note', 'OCR scores describe engine recognition. Evidence scores are review heuristics, not measured accuracy. Human edits retain the original evidence and scores.'));
  if (state.response.evaluation) content.append(make('p', 'scores-note', `Ground-truth evaluation: ${state.response.evaluation.fields_checked || 0} invoice fields checked · exact match ${ocrScore(state.response.evaluation.exact_match_accuracy)} on this labeled sample.`));
}
function renderItems(content) {
  const r = report(); const node = panel('Line items', 'Edit descriptions, quantities, rates and amounts directly. Arithmetic mismatches remain available for review.');
  if (!r.items?.length) {const blank = make('div', 'empty-state'); blank.append(make('h3', '', 'No item rows extracted'), make('p', '', 'Inspect table candidates in Structure & LLM input to see what was detected in this document.')); node.append(blank); content.append(node); return;}
  const wrap = make('div', 'table-container'); const table = make('table', 'data-table items-table'); const head = table.createTHead().insertRow();
  for (const title of ['Description', 'Quantity', 'Rate', 'Amount', 'Check', 'Source']) head.append(make('th', '', title));
  const body = table.createTBody();
  for (const [index, item] of r.items.entries()) {
    const row = body.insertRow();
    for (const field of ['description', 'quantity', 'rate', 'amount']) {
      const cell = row.insertCell(); cell.className = field === 'description' ? 'item-description' : 'item-number';
      cell.append(valueInput({label: `Item ${index + 1} ${field}`, field: {value: item[field]}, edit: {kind: 'item', index, field}}));
    }
    row.insertCell().append(badge(item.arithmetic_check === 'PASS' ? 'Matches' : item.arithmetic_check === 'MISMATCH' ? 'Mismatch' : 'Not checked', item.arithmetic_check === 'PASS' ? 'success' : 'warning'));
    row.insertCell().append(action('⌖', () => inspectSource(item.evidence, `Item ${index + 1}`), 'source-button'));
  }
  wrap.append(table); node.append(wrap); content.append(node);
}
function updateSaveBar() {
  const count = state.drafts.size;
  $('#save-help').textContent = state.modelBusy ? 'AI analysis is running. Prepare edits now; save them when it finishes.' : count ? 'Your corrections are ready to save.' : 'Type a correction to enable Save changes.';
  $('#save-status').textContent = state.saving ? 'Saving changes…' : count ? `${count} unsaved correction${count === 1 ? '' : 's'}` : 'Click a value or ✎ to edit';
  $('#save-changes').textContent = state.confirmAll ? 'Save & confirm values' : count ? `Save ${count} change${count === 1 ? '' : 's'}` : 'Save changes';
  $('#save-changes').disabled = state.saving || Boolean(state.modelBusy) || (!count && !state.confirmAll);
  $('#discard-changes').disabled = state.saving || !count;
  $('#confirm-values').disabled = state.saving || Boolean(state.modelBusy);
  $('#save-bar').classList.toggle('saving', state.saving);
}
$('#review-note').addEventListener('input', event => {state.note = event.target.value;});
$('#confirm-values').addEventListener('change', event => {state.confirmAll = event.target.checked; updateSaveBar();});
$('#discard-changes').addEventListener('click', () => {state.drafts.clear(); state.confirmAll = false; renderAnalysis();});
$('#save-changes').addEventListener('click', async () => {
  const changes = new Map(state.drafts);
  if (state.confirmAll) for (const entry of (state.section === 'items' ? entries(true).filter(entry => entry.edit.kind === 'item') : entries())) {
    const key = keyFor(entry.edit);
    if (entry.field.value != null && !changes.has(key)) changes.set(key, {...entry.edit, value: String(entry.field.value)});
  }
  if (!changes.size) return;
  const existing = new Map(entries(true).map(entry => [keyFor(entry.edit), entry]));
  const batch = [...changes].map(([key, change]) => ({...change, note: state.note || existing.get(key)?.field.human_review?.note || existing.get(key)?.field.note || ''}));
  state.saving = true; updateSaveBar();
  try {
    const saved = await post(`/api/review-batch/${state.response.run_id}`, {changes: batch, revision: state.response.revision});
    state.response = saved; state.drafts.clear(); state.confirmAll = false; state.note = ''; state.preview = null;
    renderAnalysis(); $('#save-status').textContent = `Saved ${batch.length} reviewed value${batch.length === 1 ? '' : 's'}`; refreshHistory();
  } catch (error) {notify(error.message, true);}
  finally {state.saving = false; renderAnalysis();}
});

// Optional source inspector, independent of the editable table.
function inspectSource(field, label) {
  state.selected = {...field, label}; state.sourceOpen = true;
  if (field?.page) state.page = field.page;
  renderSource();
}
function renderSource() {
  if (!report()) return;
  $('#source-panel').hidden = !state.sourceOpen;
  $('#analysis-layout').classList.toggle('with-source', state.sourceOpen);
  $('#toggle-source').textContent = state.sourceOpen ? 'Hide source' : 'Show source';
  const page = report().pages.find(p => p.number === state.page) || report().pages[0]; if (!page) return;
  state.page = page.number; const select = $('#source-page'); empty(select);
  for (const p of report().pages) select.append(new Option(`Page ${p.number}`, p.number)); select.value = String(page.number);
  $('#prev-page').disabled = page.number === report().pages[0].number;
  $('#next-page').disabled = page.number === report().pages.at(-1).number;
  $('#page-image').src = `/api/preview/${state.response.run_id}/${page.number}`;
  const field = state.selected; const highlight = $('#highlight'); highlight.hidden = true;
  if (field?.page === page.number && field.box) {
    const [x0, y0, x1, y1] = field.box; const [width, height] = page.size;
    Object.assign(highlight.style, {left: `${100 * x0 / width}%`, top: `${100 * y0 / height}%`, width: `${100 * (x1 - x0) / width}%`, height: `${100 * (y1 - y0) / height}%`}); highlight.hidden = false;
  }
  const details = $('#source-details'); empty(details);
  if (!field) {details.append(make('p', '', 'Use a field’s source button to inspect its evidence, or browse the document above.')); return;}
  details.append(make('h3', '', field.label || 'Source evidence'));
  if (field.source_text || field.text) details.append(make('div', 'source-text', field.source_text || field.text));
  const dl = make('dl');
  for (const [key, value] of [['Page', field.page], ['Method', field.method], ['Line ID', field.line_id || field.id], ['OCR', ocrScore(field.ocr_confidence)], ['Evidence', `${score(field.triage_score)} /100`]]) {dl.append(make('dt', '', key), make('dd', '', value ?? '—'));}
  details.append(dl);
  if (field.reason) details.append(make('p', '', field.reason));
  if (field.extraction_conflict) details.append(make('p', '', field.extraction_conflict));
  if (field.triage_components) {
    const fold = make('details'); fold.append(make('summary', '', 'How the evidence score is calculated'), make('pre', '', pretty(field.triage_components)), make('p', '', 'OCR 35% · label/section 35% · format 20% · consistency 10%. Missing inputs are excluded. These are heuristic weights.')); details.append(fold);
  }
  const sourceLine = report().lines.find(line => line.id === field.line_id || line.id === field.id);
  const words = field.words || sourceLine?.words;
  if (words) {
    const fold = make('details'); fold.append(make('summary', '', `Inspect ${words.length} source words`));
    for (const word of words) fold.append(action(`${word.text} · ${ocrScore(word.ocr_confidence)}`, () => {state.selected = {...field, box: word.box}; renderSource();}, 'word-chip'));
    details.append(fold);
  }
}
$('#toggle-source').addEventListener('click', () => {state.sourceOpen = !state.sourceOpen; renderSource();});
$('#close-source').addEventListener('click', () => {state.sourceOpen = false; renderSource();});
$('#source-page').addEventListener('change', event => {state.page = Number(event.target.value); renderSource();});
$('#prev-page').addEventListener('click', () => {state.page--; renderSource();});
$('#next-page').addEventListener('click', () => {state.page++; renderSource();});
$('#reload-analysis').addEventListener('click', async () => {
  if (state.drafts.size && !window.confirm('Discard unsaved edits and reload the saved analysis?')) return;
  try {
    state.response = await api(`/api/report/${state.response.run_id}`);
    state.drafts.clear(); state.confirmAll = false; state.preview = null;
    renderAnalysis(); if (state.section === 'structure') loadPreview();
    monitorJob(state.response.run_id);
  } catch (error) {notify(error.message, true);}
});
$('#download-current').addEventListener('click', () => download(report(), 'reviewed_result.json'));
$('#download-original').addEventListener('click', async () => {
  try {download(await api(`/api/original/${state.response.run_id}`), 'original_extraction.json');}
  catch (error) {notify(error.message, true);}
});

// Model connectivity is explicit, separate from whether a saved model run exists.
async function refreshModels() {
  try {
    const data = await api('/api/models'); state.models = data.models || [];
    state.modelStatus = data.status || (state.models.length ? 'ready' : 'offline');
    state.modelMessage = data.message || data.error || 'Ollama unavailable'; state.modelAction = data.action || '';
    if (!state.models.includes(state.model)) state.model = state.models.includes(report()?.llm_model) ? report().llm_model : state.models[0] || '';
  } catch (error) {state.modelStatus = 'offline'; state.modelMessage = 'Could not check Ollama.'; state.modelAction = error.message;}
  $('#connection-label').textContent = state.modelStatus === 'ready' ? 'Ollama connected' : state.modelStatus === 'no_models' ? 'No models installed' : 'Ollama offline';
  $('#connection-dot').className = `connection-dot ${state.modelStatus}`;
  $('#intake-model-status').textContent = state.modelStatus === 'ready' ? `${state.models.length} local model${state.models.length === 1 ? '' : 's'} available. Choose a model inside the analysis workspace.` : `${state.modelMessage} Document extraction still works. ${state.modelAction}`;
  $('#run-ai').disabled = state.modelStatus !== 'ready' || !state.model || Boolean(state.modelBusy) || Boolean(state.drafts.size);
  if (report() && ['model', 'structure'].includes(state.section)) {renderAnalysis(); if (state.section === 'structure') loadPreview();}
}
function modelCard() {
  const card = make('section', 'panel model-card'); const text = make('div');
  text.append(make('h2', '', 'Local model'), make('p', '', state.modelBusy ? `Ollama ${state.modelBusy === 'analyze' ? 'analysis' : state.modelBusy === 'extract' ? 'extraction' : 'review'} is running…` : state.modelStatus === 'ready' ? 'Select the Ollama model used for extraction and review.' : state.modelMessage));
  const controls = make('div', 'model-controls'); const select = make('select'); select.setAttribute('aria-label', 'Ollama model');
  if (!state.models.length) select.append(new Option(state.modelStatus === 'checking' ? 'Checking models…' : 'No models available', ''));
  for (const model of state.models) select.append(new Option(model, model)); select.value = state.model;
  select.disabled = !state.models.length || Boolean(state.modelBusy);
  select.addEventListener('change', event => {state.model = event.target.value; state.preview = null; if (state.section === 'structure') loadPreview();});
  const refresh = action('Check connection', refreshModels); refresh.disabled = Boolean(state.modelBusy);
  controls.append(select, refresh); text.append(badge(state.modelStatus === 'ready' ? 'Connected' : state.modelStatus === 'no_models' ? 'No installed model' : state.modelStatus === 'checking' ? 'Checking…' : 'Offline', state.modelStatus === 'ready' ? 'success' : 'warning'));
  card.append(text, controls); return card;
}
function runtimeAlert() {
  if (state.modelStatus === 'ready' || state.modelStatus === 'checking') return null;
  const node = make('div', 'runtime-alert'); node.append(make('h3', '', state.modelStatus === 'no_models' ? 'Install a model to enable local extraction' : 'Start Ollama to enable local extraction'), make('p', '', state.modelAction || 'Check the local Ollama service, then retry.'));
  node.append(make('code', '', state.modelStatus === 'no_models' ? 'ollama pull <model-name>' : 'ollama serve')); return node;
}
function modelButton(stage, text) {
  const button = action(text || (stage === 'extract' ? 'Run field extraction' : 'Run model review'), () => runModel(stage), 'button primary');
  button.disabled = state.modelStatus !== 'ready' || !state.model || Boolean(state.modelBusy) || Boolean(state.drafts.size) || state.saving;
  return button;
}
async function runModel(stage = 'analyze') {
  if (state.drafts.size) return notify('Save your corrections before starting a model pass.', true);
  if (!state.model) return notify('Select an installed local model first.', true);
  const runId = state.response.run_id;
  state.modelFailure = null;
  try {
    state.job = await post(`/api/model-job/${runId}`, {model: state.model, stage});
    state.modelBusy = stage; state.jobRun = runId;
    renderAnalysis(); monitorJob(runId);
  } catch (error) {state.modelFailure = error.message; renderAnalysis();}
}
async function monitorJob(runId) {
  clearTimeout(state.jobTimer); state.jobRun = runId;
  try {
    const job = await api(`/api/model-job/${runId}`);
    if (state.response?.run_id !== runId) return;
    const changed = pretty(state.job) !== pretty(job);
    state.job = job; state.modelBusy = job.status === 'running' ? job.stage : null;
    if (job.status === 'running' || job.status === 'complete' || job.status === 'failed') {
      const saved = await api(`/api/report/${runId}`);
      if (state.response?.run_id !== runId) return;
      if (!state.saving) state.response = saved;
    }
    if (changed && !state.saving) {
      if (!state.drafts.size || !['fields', 'items'].includes(state.section)) renderAnalysis();
      else {renderJobBanner(); updateSaveBar();}
      if (state.section === 'structure') loadPreview();
    }
    if (job.status === 'failed' || job.status === 'interrupted') state.modelFailure = job.message;
    if (job.status === 'running') state.jobTimer = setTimeout(() => monitorJob(runId), 2500);
    else {updateSaveBar(); refreshHistory();}
  } catch (error) {state.modelFailure = `Could not refresh AI progress: ${error.message}`; state.modelBusy = null; updateSaveBar();}
}
function renderJobBanner() {
  const node = $('#ai-progress'); node.replaceChildren();
  const job = state.job;
  node.hidden = !job || job.status === 'not_run';
  if (node.hidden) return;
  node.className = `ai-progress ${job.status}`;
  const completed = job.completed_pages?.length || 0, total = job.pages?.length || 0;
  node.append(make('strong', '', job.status === 'running' ? `AI analysis · ${completed}/${total} pages completed` : job.status === 'complete' ? 'AI analysis complete' : 'AI analysis needs attention'), make('span', '', job.message));
  if (job.status === 'running') {const progress = make('progress'); progress.max = Math.max(1, total); progress.value = completed; node.append(progress);}
  for (const warning of job.warnings || []) node.append(make('p', '', warning));
}

$('#run-ai').addEventListener('click', () => runModel('analyze'));
function pageFields(number) {
  const r = report();
  if (r.document_type === 'rate_confirmation_packet') return entries().filter(entry => entry.edit.page === number);
  return entries().filter(entry => (entry.field.page || 1) === number);
}
function initialCandidate(entry) {
  const r = report();
  const baseline = entry.edit.kind === 'rate' ? r.rule_rate_confirmations?.find(record => record.page === entry.edit.page) : r.rule_fields;
  const value = baseline?.[entry.edit.field];
  const field = Array.isArray(value) ? value[entry.edit.index] : value;
  return field?.value ?? entry.field.original_value ?? entry.field.value ?? 'Not found';
}
function pagePicker(label, callback) {
  const select = make('select'); select.setAttribute('aria-label', label);
  for (const page of report().pages) select.append(new Option(`Page ${page.number}`, page.number));
  select.value = String(state.page); select.addEventListener('change', event => {state.page = Number(event.target.value); callback();});
  return select;
}
function explanationFor(key) {
  if (key === 'carrier') return 'Read the company name after “CARRIER”. Stop at “CONTACT” so the contact person is not treated as the carrier.';
  if (key === 'order_number') return 'Use the number labelled “Order”. Keep it separate from invoice numbers and dates.';
  if (key === 'total_carrier_pay') return 'Use the printed “Total Carrier Pay” amount. Currency separators may be normalized; the amount is not invented or calculated.';
  if (key === 'invoice_references') return 'Read the invoice numbers printed in the referenced invoice section. A packet can contain several references.';
  if (key.startsWith('supplier')) return 'Use the Supplier/Seller section to identify this party; keep it separate from the buyer.';
  if (key.startsWith('buyer')) return 'Use the Bill To/Buyer section to identify the customer.';
  if (key === 'invoice_number' || key === 'invoice_date') return 'Use the corresponding invoice label and its printed value; do not substitute an order number or another date.';
  return 'Use the matching printed tax or total label, then check its source quote and arithmetic separately.';
}
function renderStructure(content) {
  content.append(modelCard()); const alert = runtimeAlert(); if (alert) content.append(alert);
  const r = report();
  const intro = panel('From OCR to an explained result', 'OCR reads the visible characters. Rules find initial candidates. The local AI interprets the selected passages and explains its field choices. Source validation checks those choices before they update the report.');
  const flow = make('div', 'readable-flow');
  for (const [title, detail] of [['1. Read the page', 'OCR text, positions and recognition confidence'], ['2. Interpret meaning', 'Identify the document and distinguish labels and parties'], ['3. Verify the quote', 'Each proposed value must occur in its cited OCR line'], ['4. Review & correct', 'AI support checks and your saved corrections']]) {
    const step = make('div'); step.append(make('b', '', title), make('p', '', detail)); flow.append(step);
  }
  intro.append(flow); content.append(intro);
  const plan = panel('Extraction plan for this page', 'This is the application’s configured plan. The AI’s actual explanation appears in AI explanation & results after analysis.');
  const tools = make('div', 'request-controls'); tools.append(pagePicker('Extraction plan page', () => {renderAnalysis(); loadPreview();}), modelButton('analyze', 'Analyze document with AI')); plan.append(tools);
  const page = r.pages.find(page => page.number === state.page);
  const type = r.document_type === 'rate_confirmation_packet' ? 'Rate-confirmation packet' : 'Invoice';
  const description = make('p', 'panel-description', `${type} · Page ${state.page} · ${page?.method === 'pdf_text' ? 'Selectable PDF text' : 'Tesseract OCR'}. ${pageFields(state.page).length ? 'The following fields are requested from the model.' : 'This page has no requested rate-confirmation fields. Its OCR remains in the transcript; model extraction runs on the relevant form pages.'}`); plan.append(description);
  const table = make('table', 'data-table plan-table'); const head = table.createTHead().insertRow();
  for (const title of ['Requested field', 'How it is distinguished', 'Initial candidate']) head.append(make('th', '', title)); const body = table.createTBody();
  for (const entry of pageFields(state.page)) {const row = body.insertRow(); row.insertCell().textContent = entry.label; row.insertCell().textContent = explanationFor(entry.edit.field); row.insertCell().textContent = initialCandidate(entry);}
  const wrap = make('div', 'table-container'); wrap.append(table); plan.append(wrap); content.append(plan);
  const passages = panel('OCR passages selected for the AI', 'These readable passages are the evidence included in the real prompt. For rate confirmations, relevant field labels, reference-section headings and candidate value lines are retrieved from the full transcript.'); passages.classList.add('section-gap');
  const passageContent = make('div', 'passage-list'); passageContent.id = 'selected-passages'; passages.append(passageContent); content.append(passages);
  const technical = make('details', 'technical-details section-gap'); technical.append(make('summary', '', 'Technical details: exact prompt, response contract and HTTP request'));
  const technicalBody = make('div'); renderTechnicalRequest(technicalBody); technical.append(technicalBody); content.append(technical);
  renderPassages();
}
function renderPassages() {
  const node = $('#selected-passages'); if (!node) return; node.replaceChildren();
  const request = state.preview?.requests?.find(request => request.page === state.page);
  if (!request) {node.append(make('p', 'subtle', state.previewError || (state.preview ? 'No model extraction request is created for this page.' : 'Loading the selected OCR passages…'))); return;}
  for (const line of request.input.lines) {
    const row = make('div', 'passage'); row.append(make('p', '', line.text));
    const button = action('View on page', () => {const source = report().lines.find(item => item.id === (request.source_map?.[line.id] || line.id)); if (source) inspectSource({...source, source_text: source.text}, 'OCR passage');}, 'text-link'); row.append(button); node.append(row);
  }
}
function renderEvidence(content) {
  const node = panel('OCR transcript — what the page reader found', 'This is the text read from the original document before rules or AI interpret it. OCR can contain spelling errors or misplaced lines. It is evidence for extraction, not an AI answer, and it does not change when you correct a field.');
  const tools = make('div', 'request-controls'); tools.append(pagePicker('OCR transcript page', () => renderAnalysis()), action('View original page', () => {state.selected = null; state.sourceOpen = true; renderSource();})); node.append(tools);
  const transcript = make('div', 'ocr-transcript');
  for (const line of report().lines.filter(line => line.page === state.page)) {
    const passage = make('div', 'transcript-line'); passage.append(make('p', '', line.text));
    passage.append(action('Locate', () => inspectSource({...line, source_text: line.text}, 'OCR passage'), 'text-link')); transcript.append(passage);
  }
  node.append(transcript); content.append(node);
}

// Structure shows the exact prompt, schema, and body sent by the backend builder.
function renderTechnicalRequest(content) {
  const r = report();
  const node = panel('Exact request details', 'For technical inspection: the prompt, JSON contract and request body sent to Ollama.');
  const stats = make('div', 'structure-stats');
  for (const [value, label] of [[r.lines.length, 'lines'], [r.structure?.blocks?.length || 0, 'blocks'], [r.structure?.table_candidates?.length || 0, 'table candidates']]) {const span = make('span'); span.append(make('b', '', value), document.createTextNode(label)); stats.append(span);} node.append(stats);
  const controls = make('div', 'request-controls'); const stage = make('select'); stage.setAttribute('aria-label', 'Request stage'); stage.append(new Option('Field extraction', 'extract'), new Option('Model review', 'review')); stage.value = state.requestStage;
  stage.addEventListener('change', event => {state.requestStage = event.target.value; renderAnalysis(); loadPreview();});
  const page = make('select'); page.setAttribute('aria-label', 'LLM input page');
  for (const p of r.pages) page.append(new Option(`Page ${p.number}`, p.number)); page.value = String(state.page);
  page.addEventListener('change', event => {state.page = Number(event.target.value); renderCode();});
  controls.append(make('label', '', 'Request'), stage, make('label', '', 'Page'), page, modelButton(state.requestStage, state.requestStage === 'review' ? 'Run model review' : 'Extract fields'));
  node.append(controls);
  const tabs = make('div', 'code-tabs');
  for (const [key, label] of [['prompt', 'Prompt'], ['input', 'Structured input'], ['schema', 'Response schema'], ['request', 'Full request']]) {
    const button = action(label, () => {state.codeTab = key; renderCode();}, state.codeTab === key ? 'active' : ''); button.dataset.codeTab = key; tabs.append(button);
  }
  node.append(tabs); const meta = make('div', 'code-meta'); meta.id = 'code-meta'; node.append(meta);
  const code = make('pre', 'code-view wrap', 'Preparing the request preview…'); code.id = 'request-code'; node.append(code);
  node.append(make('p', 'payload-explanation', 'The prompt contains structured source evidence. The response schema asks for each field’s value, a supporting OCR line and a reason. Returned citations and values are checked against the source before they become editable fields. Previewing the request never calls the model.'));
  content.append(node);
  if (state.drafts.size) content.append(make('p', 'scores-note', 'Your unsaved edits are excluded from this preview. Save them before a model review.'));
  if (state.modelBusy) content.append(make('p', 'notice', `Running Ollama ${state.modelBusy}. Large documents can take a few minutes.`));
  if (state.modelFailure) content.append(make('p', 'notice error', state.modelFailure));
  if (!r.structure) content.append(make('p', 'notice error', 'This older analysis has no saved structure or word evidence. Reanalyze its source document to use the new field-extraction request.'));
  else if (r.structure.table_candidates.length) {
    const tables = panel('Detected table candidates', 'Provisional layout groups retained before field extraction.'); tables.classList.add('section-gap');
    const table = make('table', 'data-table'); const head = table.createTHead().insertRow();
    for (const title of ['Page', 'Headers', 'Rows', 'Source']) head.append(make('th', '', title)); const body = table.createTBody();
    for (const candidate of r.structure.table_candidates) {const row = body.insertRow(); row.insertCell().textContent = candidate.page; row.insertCell().textContent = candidate.headers.join(' · '); row.insertCell().textContent = candidate.rows.length; row.insertCell().append(action('Inspect', () => inspectSource({...candidate, source_text: candidate.rows.map(r => r.text).join('\n')}, candidate.id), 'text-link'));}
    tables.append(table); content.append(tables);
  }
  renderCode();
}
async function loadPreview() {
  if (!state.response || state.section !== 'structure') return;
  const sequence = ++state.previewSequence; state.previewError = null; state.preview = null; renderCode();
  try {
    const data = await post(`/api/llm-preview/${state.response.run_id}`, {model: state.model || '<select-installed-model>', stage: state.requestStage});
    if (sequence !== state.previewSequence) return; state.preview = data;
  } catch (error) {if (sequence !== state.previewSequence) return; state.preview = null; state.previewError = error.message;}
  renderCode();
}
function renderCode() {
  if (!$('#request-code')) return;
  for (const button of document.querySelectorAll('[data-code-tab]')) button.classList.toggle('active', button.dataset.codeTab === state.codeTab);
  const code = $('#request-code'); const meta = $('#code-meta'); empty(meta);
  renderPassages();
  if (state.previewError) {code.textContent = state.previewError; return;}
  const request = state.preview?.requests.find(r => r.page === state.page);
  if (!request) {code.textContent = state.preview ? 'No request is generated for this page in the selected stage. Select a form page or switch stages.' : 'Preparing the request preview…'; return;}
  renderPassages();
  const pageInput = request.input;
  const text = state.codeTab === 'prompt' ? request.body.prompt : state.codeTab === 'schema' ? pretty(request.body.format) : state.codeTab === 'input' ? pretty(pageInput) : pretty(request.body);
  code.classList.toggle('wrap', state.codeTab === 'prompt'); code.textContent = text;
  meta.append(make('span', '', `POST ${request.endpoint} · ${request.body.model} · ${request.body.prompt.length.toLocaleString()} prompt characters`), action('Download request', () => download(request.body, `ollama-${state.requestStage}-page-${state.page}.json`), 'text-link'));
}
function renderModelResults(content) {
  const r = report(); content.append(modelCard()); const alert = runtimeAlert(); if (alert) content.append(alert);
  if (state.modelFailure || r.llm_error) content.append(make('p', 'notice error', state.modelFailure || r.llm_error));
  if (state.modelBusy) content.append(make('p', 'notice', `Ollama ${state.modelBusy === 'analyze' ? 'analysis' : state.modelBusy === 'extract' ? 'extraction' : 'review'} is running. Completed pages appear as they finish.`));
  const actions = panel('Extraction & review'); const row = make('div', 'panel-footer'); row.append(modelButton('analyze', r.llm_extraction?.length ? 'Run full AI analysis again' : 'Run full AI analysis'), modelButton('extract', r.llm_extraction?.length ? 'Run extraction again' : 'Run field extraction'), modelButton('review', r.llm_review?.length ? 'Run review again' : 'Run model review')); actions.append(row); content.append(actions);
  if (state.drafts.size) content.append(make('p', 'scores-note', 'Save or discard your field edits before running a model pass.'));
  const hasExtraction = r.llm_extraction?.length; const hasReview = r.llm_review?.length;
  if (!hasExtraction && !hasReview) {
    const blank = make('section', 'panel empty-state model-empty section-gap');
    blank.append(make('h3', '', state.modelStatus === 'ready' ? 'This document has not been analyzed by AI yet' : 'Model results are pending'), make('p', '', state.modelStatus === 'ready' ? 'Your current fields were found by OCR and rules. Run full AI analysis above to obtain a document explanation, source-backed proposals and a separate verification of the values.' : 'Ollama is currently unavailable. This is a connection state, not an empty model response. Existing extracted fields remain editable.'));
    const link = make('a', 'button secondary', 'See how extraction works →'); link.href = `#/analysis/${state.response.run_id}/structure`; blank.append(link); content.append(blank); return;
  }
  if (hasExtraction) for (const extraction of r.llm_extraction) {
    const meaning = extraction.interpretation;
    const interpretation = panel(`AI interpretation · Page ${extraction.page}`, `Generated by ${extraction.model}. This explanation is the model’s interpretation; validated field quotes are shown separately below.`); interpretation.classList.add('section-gap');
    interpretation.append(make('p', 'model-summary', meaning?.summary || 'This older response has no document explanation. Run full AI analysis to generate one.'));
    if (meaning?.extraction_approach) {interpretation.append(make('h3', 'interpretation-label', 'How the model distinguished the fields'), make('p', 'model-summary', meaning.extraction_approach));}
    content.append(interpretation);
  }
  if (hasReview) {
    const node = panel('Evidence score vs model review', 'The model score is a self-assessed support score. Its explanation is shown alongside the evidence heuristic; neither is measured accuracy.'); node.classList.add('section-gap');
    const wrap = make('div', 'table-container'); const table = make('table', 'data-table comparison-table'); const head = table.createTHead().insertRow();
    for (const title of ['Field', 'Reviewed value', 'Evidence /100', 'Model /100', 'Model reason']) head.append(make('th', '', title)); const body = table.createTBody();
    for (const review of r.llm_review) for (const [key, value] of Object.entries(review.fields)) {
      const row = body.insertRow(); row.insertCell().textContent = `${fieldLabels[key] || key} · p${review.page}`;
      row.insertCell().textContent = Array.isArray(value.reviewed_value) ? value.reviewed_value.join(', ') : value.reviewed_value ?? value.source_text ?? value.source_texts?.join(', ') ?? 'Not extracted';
      row.insertCell().textContent = score(value.evidence_score); row.insertCell().textContent = score(value.llm_score);
      const reason = row.insertCell(); reason.className = 'reason-cell'; reason.textContent = value.reason || `Legacy evidence-line comparison. Agrees with rules: ${value.agrees_with_rules}.`;
    }
    wrap.append(table); node.append(wrap); content.append(node);
  }
  if (hasExtraction) {
    const node = panel('Model extraction', 'Source-validated proposals can update fields. Rejected proposals stay visible with their reason. Human-reviewed values are protected.'); node.classList.add('section-gap');
    const wrap = make('div', 'table-container'); const table = make('table', 'data-table'); const head = table.createTHead().insertRow();
    for (const title of ['Field', 'Proposed value', 'Validation', 'Model explanation', 'Page']) head.append(make('th', '', title)); const body = table.createTBody();
    for (const extraction of r.llm_extraction) for (const [key, proposal] of Object.entries(extraction.fields)) {
      const accepted = Array.isArray(proposal) ? proposal : proposal ? [proposal] : [];
      const rejected = extraction.rejected_proposals?.[key] || [];
      const entries = [...accepted.map(field => ({field, valid: true})), ...rejected.map(entry => ({field: entry.proposal, reason: entry.reason, valid: false}))];
      if (!entries.length) entries.push({field: Array.isArray(extraction.raw_response?.[key]) ? null : extraction.raw_response?.[key], valid: false});
      for (const entry of entries) {
        const row = body.insertRow(); row.insertCell().textContent = fieldLabels[key] || key;
        row.insertCell().textContent = entry.field?.value ?? 'Not found';
        const status = row.insertCell(); status.append(badge(entry.valid ? 'Source validated' : entry.reason ? 'Rejected' : 'Not found', entry.valid ? 'success' : entry.reason ? 'warning' : ''));
        if (entry.reason || entry.field?.source_selection_note) status.append(make('p', 'subtle', entry.reason || entry.field.source_selection_note));
        const reason = row.insertCell(); reason.className = 'reason-cell'; reason.textContent = entry.field?.reason || 'The model did not find a supported value.';
        if (entry.field?.source_text) {
          const sourceLine = r.lines.find(line => line.id === entry.field.line_id);
          reason.append(make('blockquote', 'source-quote', sourceLine?.text || entry.field.source_text));
          reason.append(action('View supporting passage', () => inspectSource(entry.field, fieldLabels[key] || key), 'text-link'));
        }
        if (entry.valid && Array.isArray(proposal) && rejected.length) status.append(make('p', 'subtle', 'Reference list retained: another proposal was rejected.'));
        row.insertCell().textContent = extraction.page;
      }
    }
    wrap.append(table); node.append(wrap);
    for (const extraction of r.llm_extraction) {
      const raw = make('details', 'detail-json'); raw.append(make('summary', '', `Raw response · page ${extraction.page} · ${extraction.model}`), make('pre', 'code-view', pretty(extraction.raw_response))); node.append(raw);
    }
    content.append(node);
  }
}

if (!location.hash) location.hash = '#/new';
else route();
Promise.all([refreshHistory(), refreshModels()]);
