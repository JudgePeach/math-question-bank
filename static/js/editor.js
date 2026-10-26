// Sidebar Pagination & Sorting Global State
let currentBankPage = 1;
let currentDraftPage = 1;
const PAGE_LIMIT = 20;
let bankQuestionsLoadController = null;
let bankQuestionsLoadSequence = 0;
let bankQuestionsRetryTimer = null;
let sidebarSearchTimer = null;
const SIDEBAR_SEARCH_DELAY_MS = 300;

function cancelPendingSidebarSearch() {
    if (sidebarSearchTimer !== null) clearTimeout(sidebarSearchTimer);
    sidebarSearchTimer = null;
}

function invalidatePendingBankQuestions() {
    ++bankQuestionsLoadSequence;
    if (bankQuestionsRetryTimer) clearTimeout(bankQuestionsRetryTimer);
    bankQuestionsRetryTimer = null;
    if (bankQuestionsLoadController) bankQuestionsLoadController.abort();
    bankQuestionsLoadController = null;
    document.getElementById('questionsList')?.removeAttribute('aria-busy');
}

function runSidebarSearchImmediately() {
    cancelPendingSidebarSearch();
    currentBankPage = currentDraftPage = 1;
    if (activeSidebarTab === 'bank') loadQuestions();
    else loadDrafts();
}

function scheduleSidebarSearch() {
    cancelPendingSidebarSearch();
    // Invalidate now: an old response must not render during the debounce wait.
    invalidatePendingBankQuestions();
    currentBankPage = currentDraftPage = 1;
    sidebarSearchTimer = setTimeout(runSidebarSearchImmediately, SIDEBAR_SEARCH_DELAY_MS);
}

// A subdivision always carries its complete curriculum path. Legacy category
// fields and free-form tags remain separate, and selections belong to a version.
let subKnowledgeCatalog = [];
let subKnowledgeCatalogRequest = 0;
let editorSubKnowledgeByVersion = {};
let editorClassificationByVersion = {};
function normalizeSubKnowledge(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.filter(item => item && typeof item === 'object' && !Array.isArray(item))
        .map(item => ({ compulsory: String(item.compulsory || '').trim(),
            chapter: String(item.chapter || '').trim(), knowledge: String(item.knowledge || '').trim() }))
        .filter(item => item.compulsory && item.chapter && item.knowledge)
        .filter(item => { const key = JSON.stringify(item); if (seen.has(key)) return false; seen.add(key); return true; });
}
function subKnowledgeSignature(value) {
    return JSON.stringify(normalizeSubKnowledge(value).map(item => JSON.stringify(item)).sort());
}
function subKnowledgeVersionsSignature(value) {
    return JSON.stringify(Object.entries(value || {}).map(([version, paths]) => [version, subKnowledgeSignature(paths)])
        .filter(([, signature]) => signature !== '[]').sort((left, right) => left[0].localeCompare(right[0])));
}
function subKnowledgePathLabel(path) {
    return [path.compulsory, path.chapter, path.knowledge].join(' / ');
}
function getSubKnowledgeSelection(kind) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    if (!container) return [];
    const paths = Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(input => {
        try { return JSON.parse(input.value); } catch (_) { return null; }
    });
    return normalizeSubKnowledge(paths);
}
function getEditorSubKnowledge() { return getSubKnowledgeSelection('edit'); }
function getFilterSubKnowledge() { return getSubKnowledgeSelection('filter'); }
function directorySubKnowledgePaths() {
    const result = [];
    Object.entries(categoryTree || {}).forEach(([compulsory, chapters]) => {
        Object.entries(chapters || {}).forEach(([chapter, values]) => {
            if (Array.isArray(values)) values.forEach(knowledge => result.push({compulsory, chapter, knowledge}));
        });
    });
    return normalizeSubKnowledge(result);
}
function refreshSubKnowledgeSummary(kind) {
    const summary = document.getElementById(`${kind}SubKnowledgeSummary`);
    if (!summary) return;
    const count = getSubKnowledgeSelection(kind).length;
    summary.textContent = count ? `细分知识点（已选 ${count} 项）` : '细分知识点（可多选）';
}
function searchSubKnowledgeOptions(kind, value) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    const query = String(value || '').trim().toLowerCase();
    if (container) Array.from(container.querySelectorAll('label')).forEach(label => {
        label.hidden = Boolean(query) && !label.textContent.toLowerCase().includes(query);
    });
}
function renderSubKnowledgeOptions(kind, selected = null) {
    const container = document.getElementById(`${kind}SubKnowledgeOptions`);
    if (!container) return;
    const paths = normalizeSubKnowledge(selected === null ? getSubKnowledgeSelection(kind) : selected);
    const checked = new Set(paths.map(path => JSON.stringify(path)));
    const candidates = normalizeSubKnowledge([...paths, ...directorySubKnowledgePaths(), ...subKnowledgeCatalog]);
    container.replaceChildren();
    candidates.forEach(path => {
        const label = document.createElement('label');
        label.className = 'sub-knowledge-option';
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = JSON.stringify(path);
        input.checked = checked.has(input.value);
        input.addEventListener('change', () => {
            refreshSubKnowledgeSummary(kind);
            if (kind === 'edit') {
                if (window.activeCurriculumVersion) editorSubKnowledgeByVersion[window.activeCurriculumVersion] = getEditorSubKnowledge();
                renderEditorPaperMeta();
                refreshEditorFeedback();
            } else if (kind === 'filter') {
                currentBankPage = currentDraftPage = 1;
                updateBankFilterSummary();
                if (activeSidebarTab === 'bank') loadQuestions(); else loadDrafts();
            } else if (kind === 'paper' && typeof window.onPaperFilterChange === 'function') {
                window.onPaperFilterChange('sub_knowledge', getSubKnowledgeSelection(kind));
            }
        });
        const text = document.createElement('span');
        text.textContent = subKnowledgePathLabel(path);
        label.append(input, text);
        container.appendChild(label);
    });
    if (!candidates.length) {
        const empty = document.createElement('p');
        empty.className = 'sub-knowledge-empty';
        empty.textContent = '当前目录暂无候选，可填写完整路径添加。';
        container.appendChild(empty);
    }
    refreshSubKnowledgeSummary(kind);
    searchSubKnowledgeOptions(kind, document.getElementById(`${kind}SubKnowledgeSearch`)?.value);
}
function setEditorSubKnowledge(value) {
    const paths = normalizeSubKnowledge(value);
    const version = window.activeCurriculumVersion || window.editorCurriculumVersion;
    if (version) editorSubKnowledgeByVersion[version] = paths;
    renderSubKnowledgeOptions('edit', paths);
}
function resetEditorSubKnowledge() {
    editorSubKnowledgeByVersion = {};
    editorClassificationByVersion = {};
    window.editorCurriculumVersion = window.activeCurriculumVersion || '';
    setEditorSubKnowledge([]);
}
function hydrateEditorSubKnowledge(record, isDraft = false) {
    editorSubKnowledgeByVersion = {};
    editorClassificationByVersion = isDraft && record?.classification_by_version ? {...record.classification_by_version} : {};
    if (isDraft && record && record.sub_knowledge_by_version) {
        Object.entries(record.sub_knowledge_by_version).forEach(([version, paths]) => {
            editorSubKnowledgeByVersion[version] = normalizeSubKnowledge(paths);
        });
    }
    window.editorCurriculumVersion = isDraft ? (window.activeCurriculumVersion || '')
        : (record?.active_version_code || window.activeCurriculumVersion || '');
    if (window.editorCurriculumVersion) editorClassificationByVersion[window.editorCurriculumVersion] = {
        category_compulsory: document.getElementById('editCompulsory')?.value || '',
        category_chapter: document.getElementById('editChapter')?.value || '',
        category_knowledge: document.getElementById('editKnowledge')?.value || ''
    };
    setEditorSubKnowledge(isDraft ? subKnowledgeForDraft(record) : record?.sub_knowledge || []);
}
function editorSubKnowledgeVersionSnapshot() {
    const result = {};
    Object.entries(editorSubKnowledgeByVersion).forEach(([version, paths]) => { result[version] = normalizeSubKnowledge(paths); });
    const version = window.activeCurriculumVersion || window.editorCurriculumVersion;
    if (version) result[version] = getEditorSubKnowledge();
    return result;
}
function editorClassificationVersionSnapshot() {
    const result = JSON.parse(JSON.stringify(editorClassificationByVersion));
    const version = window.editorCurriculumVersion || window.activeCurriculumVersion;
    if (version) result[version] = {
        category_compulsory: document.getElementById('editCompulsory')?.value || '',
        category_chapter: document.getElementById('editChapter')?.value || '',
        category_knowledge: document.getElementById('editKnowledge')?.value || ''
    };
    return result;
}
function draftClassification(draft) {
    const version = window.activeCurriculumVersion || '';
    return draft?.classification_by_version?.[version]
        || (!draft?.version_code || draft.version_code === version ? draft : {});
}
function addEditorSubKnowledge() {
    const fields = ['Compulsory', 'Chapter', 'Knowledge'].map(key => document.getElementById(`editSubKnowledgeCustom${key}`));
    if (fields.some(field => !field || !field.value.trim())) {
        showToast('请填写细分知识点的学段、章节和小节完整路径。', 'info');
        fields.find(field => field && !field.value.trim())?.focus();
        return;
    }
    const path = {compulsory: fields[0].value, chapter: fields[1].value, knowledge: fields[2].value};
    setEditorSubKnowledge([...getEditorSubKnowledge(), path]);
    fields.forEach(field => { field.value = ''; });
    const picker = document.getElementById('editSubKnowledgePicker');
    if (picker) picker.open = true;
    renderEditorPaperMeta();
    refreshEditorFeedback();
}
function subKnowledgeForDraft(draft) {
    const version = window.activeCurriculumVersion || '';
    const stored = draft && draft.sub_knowledge_by_version;
    if (stored && typeof stored === 'object' && version) return normalizeSubKnowledge(stored[version]);
    return draft && draft.version_code && draft.version_code === version ? normalizeSubKnowledge(draft.sub_knowledge) : [];
}
function matchesQuestionCurriculumFilters(item, filters) {
    const path = {compulsory: item.category_compulsory || '', chapter: item.category_chapter || '', knowledge: item.category_knowledge || ''};
    return ['compulsory', 'chapter', 'knowledge'].every(key => !filters[key] || path[key] === filters[key]);
}
function renderSubKnowledgeBadges(value, compact = false) {
    const paths = normalizeSubKnowledge(value);
    if (!paths.length) return '';
    const shown = compact ? paths.slice(0, 2) : paths;
    const badges = shown.map(path => `<span class="sub-knowledge-badge" title="${window.MathBankSafe.escapeAttribute(subKnowledgePathLabel(path))}">细分：${window.MathBankSafe.escapeText(subKnowledgePathLabel(path))}</span>`).join('');
    const more = compact && paths.length > shown.length ? `<span class="sub-knowledge-badge" title="${window.MathBankSafe.escapeAttribute(paths.map(subKnowledgePathLabel).join('；'))}">+${paths.length - shown.length}</span>` : '';
    return `<div class="sub-knowledge-badges">${badges}${more}</div>`;
}
async function loadSubKnowledgeCatalog() {
    const request = ++subKnowledgeCatalogRequest;
    const response = await fetch('/api/config/sub-knowledge');
    if (!response.ok) throw new Error('细分知识点目录加载失败');
    const data = await response.json();
    if (request !== subKnowledgeCatalogRequest) return null;
    const previousVersion = window.activeCurriculumVersion || window.editorCurriculumVersion;
    const nextVersion = typeof data.version_code === 'string' ? data.version_code : '';
    if (!nextVersion) throw new Error('教材版本信息缺失');
    const versionChanged = Boolean(previousVersion && previousVersion !== nextVersion);
    const editorWasModified = typeof isEditorModified === 'function' && isEditorModified();
    if (previousVersion) editorSubKnowledgeByVersion[previousVersion] = getEditorSubKnowledge();
    window.activeCurriculumVersion = nextVersion;
    subKnowledgeCatalog = normalizeSubKnowledge(data.sub_knowledge);
    if (versionChanged) {
        setEditorSubKnowledge(editorSubKnowledgeByVersion[nextVersion] || []);
        // A clean question may now reload its new-version mirror. Preserve a
        // dirty session and require its original version before saving it.
        if (!editorWasModified && originalQuestionState) {
            originalQuestionState.sub_knowledge = getEditorSubKnowledge();
            originalQuestionState.sub_knowledge_by_version = editorSubKnowledgeVersionSnapshot();
        }
        renderSubKnowledgeOptions('filter', []);
        currentBankPage = currentDraftPage = 1;
        if (window.PaperStore) window.PaperStore.filters.sub_knowledge = [];
    } else {
        renderSubKnowledgeOptions('edit');
        renderSubKnowledgeOptions('filter');
    }
    if (!window.editorCurriculumVersion) window.editorCurriculumVersion = nextVersion;
    if (window.PaperStore) renderSubKnowledgeOptions('paper', window.PaperStore.filters.sub_knowledge || []);
    return {...data, version_changed: versionChanged};
}
Object.assign(window, {normalizeSubKnowledge, subKnowledgeSignature, subKnowledgeVersionsSignature, subKnowledgePathLabel, getEditorSubKnowledge,
    getFilterSubKnowledge, getSubKnowledgeSelection, renderSubKnowledgeOptions, setEditorSubKnowledge,
    resetEditorSubKnowledge, hydrateEditorSubKnowledge, editorSubKnowledgeVersionSnapshot,
    editorClassificationVersionSnapshot, draftClassification,
    searchSubKnowledgeOptions, addEditorSubKnowledge, loadSubKnowledgeCatalog, subKnowledgeForDraft,
    matchesQuestionCurriculumFilters, renderSubKnowledgeBadges});

// Formatting operates on one editor snapshot; delayed replies cannot replace
// newer typing or a different question/draft, and never advance the saved state.
async function normalizeEditorFractions(target, button) {
    if (!['editContent', 'editAnswerMarkdown'].includes(target)) return;
    const input = document.getElementById(target);
    if (!input || !button || button.disabled) return;
    const source = input.value;
    if (!source.trim()) {
        showToast('请先输入需要规范分式的内容。', 'info');
        return;
    }
    const session = EditorState.snapshot();
    let edited = false;
    const markEdited = () => { edited = true; };
    input.addEventListener('input', markEdited);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
        const form = new FormData();
        form.append('text', source);
        const response = await fetch('/api/format/fractions', { method: 'POST', body: form });
        if (!response.ok) throw new Error('fraction formatting failed');
        const result = await response.json();
        if (!EditorState.isCurrent(session)) return;
        if (edited || input.value !== source) {
            showToast('内容已变化，请重新点击“规范分式”。', 'info');
            return;
        }
        if (typeof result.text !== 'string') throw new Error('invalid formatting response');
        if (result.text === source) {
            showToast('未发现可调整的分式；仅处理完整数学环境中的标准分式。', 'info');
            return;
        }
        input.value = result.text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (typeof refreshEditorFeedback === 'function') refreshEditorFeedback();
        showToast('分式已规范，请核对预览后保存。', 'success');
    } catch (error) {
        if (EditorState.isCurrent(session)) {
            showToast('分式规范失败，原内容已保留，请稍后重试。', 'error');
        }
    } finally {
        input.removeEventListener('input', markEdited);
        button.disabled = false;
        button.removeAttribute('aria-busy');
    }
}
window.normalizeEditorFractions = normalizeEditorFractions;

// Shared point directory and picker. Names, paths and evidence are text nodes;
// selecting a point never changes the question's curriculum location or tags.
const knowledgePointCatalogCache = new Map();
const knowledgePointViews = new Map();
const knowledgePointNames = new Map();
function knowledgePointVersion() { return window.activeCurriculumVersion || window.editorCurriculumVersion || 'A'; }
function knowledgePointName(code) { return knowledgePointNames.get(code) || code; }
function knowledgePointNode(tag, text = '', className = '') {
    const node = document.createElement(tag); node.textContent = text;
    if (className) node.className = className;
    return node;
}
function knowledgePointButton(text, action) {
    const node = knowledgePointNode('button', text, 'glass-btn mb-kp-button'); node.type = 'button';
    node.addEventListener('click', event => { event.stopPropagation(); action(event); }); return node;
}
function currentKnowledgePointLocation(kind) {
    if (kind === 'paper') {
        const f = window.PaperStore?.filters || {};
        return {compulsory: f.compulsory || '', chapter: f.chapter || '', knowledge: f.knowledge || ''};
    }
    const prefix = kind === 'edit' ? 'edit' : 'filter';
    return {compulsory: document.getElementById(prefix + 'Compulsory')?.value || '',
        chapter: document.getElementById(prefix + 'Chapter')?.value || '',
        knowledge: document.getElementById(prefix + 'Knowledge')?.value || ''};
}
async function loadKnowledgePointCatalog(force = false) {
    const version = knowledgePointVersion();
    let entry = knowledgePointCatalogCache.get(version);
    if (!force && entry?.ready) {
        entry.items.forEach(item => knowledgePointNames.set(item.code, item.name));
        return entry.items;
    }
    if (!force && entry?.promise) return entry.promise;
    entry = {items: entry?.items || [], hierarchy: entry?.hierarchy || [], ready: false, error: '', promise: null};
    knowledgePointCatalogCache.set(version, entry);
    entry.promise = (async () => {
        try {
            const items = [], codes = new Set();
            let total = null, hierarchy = [];
            while (total === null || items.length < total) {
                const offset = items.length;
                const params = new URLSearchParams({version_code: version, limit: '1000', offset: String(offset)});
                const response = await fetch('/api/knowledge-points?' + params.toString());
                if (!response.ok) throw new Error('知识点目录加载失败');
                const data = await response.json();
                if (!data || !Array.isArray(data.items) || data.items.length > 1000
                    || !Number.isSafeInteger(data.total) || data.total < 0
                    || (data.version_code !== undefined && data.version_code !== version)
                    || (data.offset !== undefined && data.offset !== offset)) throw new Error('知识点目录响应无效');
                if (total !== null && data.total !== total) throw new Error('知识点目录已变化，请重新加载');
                total = data.total;
                if (offset === 0 && data.hierarchy !== undefined) {
                    if (!Array.isArray(data.hierarchy)) throw new Error('知识点目录层级无效');
                    const locations = new Set();
                    for (const row of data.hierarchy) {
                        if (!row || !['compulsory','chapter','knowledge'].every(field => typeof row[field] === 'string' && row[field].trim())
                            || !Array.isArray(row.ancestors) || row.ancestors.some(value => typeof value !== 'string' || !value.trim())
                            || new Set(row.ancestors).size !== row.ancestors.length || row.ancestors.includes(row.knowledge)) {
                            throw new Error('知识点目录层级无效');
                        }
                        const key = JSON.stringify([row.compulsory,row.chapter,row.knowledge]);
                        if (locations.has(key)) throw new Error('知识点目录层级重复');
                        locations.add(key);
                    }
                    hierarchy = data.hierarchy;
                }
                if (!data.items.length && items.length < total) throw new Error('知识点目录不完整，请重新加载');
                for (const item of data.items) {
                    if (!item || typeof item.code !== 'string' || !item.code.trim()
                        || typeof item.name !== 'string' || !item.name.trim() || codes.has(item.code)) {
                        throw new Error('知识点目录编码重复或项目无效');
                    }
                    codes.add(item.code); items.push(item);
                }
                if (items.length > total) throw new Error('知识点目录数量不一致');
            }
            // Publish one complete catalogue. A failed later page must never
            // replace the last complete cache with a partial tree.
            entry.items = items;
            entry.hierarchy = hierarchy;
            if (knowledgePointCatalogCache.get(version) === entry && version === knowledgePointVersion()) {
                items.forEach(item => knowledgePointNames.set(item.code, item.name));
            }
            entry.ready = true;
            return entry.items;
        } catch (error) { entry.error = error.message; throw error; }
        finally {
            entry.promise = null;
            if (knowledgePointCatalogCache.get(version) === entry && version === knowledgePointVersion()) renderKnowledgePointViews();
        }
    })();
    renderKnowledgePointViews();
    return entry.promise;
}
function reloadBankKnowledgePointFilters() {
    currentBankPage = currentDraftPage = 1;
    updateBankFilterSummary();
    if (activeSidebarTab === 'bank') loadQuestions(); else loadDrafts();
}
function updateKnowledgePointFilter(kind, patch) {
    KnowledgePointState.setFilter(kind, patch);
    renderKnowledgePointPicker(kind);
    if (kind === 'paper' && typeof window.onPaperFilterChange === 'function') {
        window.onPaperFilterChange('knowledge_point_codes', KnowledgePointState.filter(kind).knowledge_point_codes);
    } else reloadBankKnowledgePointFilters();
}
function positionKnowledgePointPicker(view) {
    if (view.picker.hidden) return;
    const anchor = view.toggle.getBoundingClientRect();
    if (!anchor.width || !anchor.height) { setKnowledgePointPickerOpen(view, false); return; }
    const viewport = window.visualViewport;
    const leftEdge = (viewport?.offsetLeft || 0) + 12;
    const topEdge = (viewport?.offsetTop || 0) + 12;
    const rightEdge = leftEdge + (viewport?.width || innerWidth) - 24;
    const bottomEdge = topEdge + (viewport?.height || innerHeight) - 24;
    const width = Math.min(520, rightEdge - leftEdge);
    const below = Math.max(0, bottomEdge - anchor.bottom - 6);
    const above = Math.max(0, anchor.top - topEdge - 6);
    const preferredHeight = view.options.classList.contains('mb-kp-tree') ? 660 : 430;
    const opensBelow = below >= Math.min(preferredHeight, above);
    const maximumHeight = Math.max(44, Math.min(preferredHeight, opensBelow ? below : above));
    view.picker.style.width = width + 'px';
    view.picker.style.left = Math.max(leftEdge, Math.min(anchor.left, rightEdge - width)) + 'px';
    view.picker.style.setProperty('--mb-kp-panel-height', maximumHeight + 'px');
    const height = view.picker.getBoundingClientRect().height;
    const top = opensBelow ? anchor.bottom + 6 : anchor.top - 6 - height;
    view.picker.style.top = Math.max(topEdge, Math.min(top, bottomEdge - height)) + 'px';
}
function setKnowledgePointPickerOpen(view, open) {
    if (open) knowledgePointViews.forEach(other => { if (other !== view) setKnowledgePointPickerOpen(other, false); });
    view.picker.open = Boolean(open); view.picker.hidden = !open;
    view.toggle.setAttribute('aria-expanded', String(Boolean(open)));
    view.host.classList.toggle('mb-kp-open', Boolean(open));
    if (open) {
        view.panel.scrollTop = 0; renderKnowledgePointOptions(view);
        loadKnowledgePointCatalog().catch(() => {});
        view.search.focus({preventScroll: true});
    }
}
function mountKnowledgePointPicker(kind, host) {
    if (!host) return;
    const old = knowledgePointViews.get(kind);
    if (old?.host === host && host.childNodes.length) { renderKnowledgePointPicker(kind); return; }
    old?.picker.remove();
    const view = {host, kind, query: old?.query || '', scope: old?.scope || 'current', page: old?.page || 0, menuCode: '',
        treeExpanded: old?.treeExpanded || new Map()};
    host.replaceChildren();
    host.classList.add('mb-knowledge-point-field');
    host.classList.toggle('mb-kp-edit', kind === 'edit');
    host.classList.remove('mb-kp-open', 'mb-kp-create-open');
    const header = knowledgePointNode('div', '', 'mb-kp-header');
    const heading = knowledgePointNode('div', '知识点', 'mb-kp-heading');
    view.selected = knowledgePointNode('div', '', 'mb-kp-selected');
    view.selected.setAttribute('aria-live', 'polite');
    view.menu = knowledgePointNode('div', '', 'mb-kp-menu');
    view.menu.hidden = true;
    view.picker = knowledgePointNode('div', '', 'mb-kp-picker'); view.picker.id = 'mb-kp-picker-' + kind;
    view.picker.open = false; view.picker.hidden = true;
    view.toggle = knowledgePointButton(kind === 'edit' ? '选择知识点' : '选择／设置', () => setKnowledgePointPickerOpen(view, view.picker.hidden));
    view.toggle.classList.add('mb-kp-toggle'); view.toggle.setAttribute('aria-controls', view.picker.id);
    view.toggle.setAttribute('aria-expanded', 'false');
    header.append(heading, view.toggle, view.selected); host.append(header);
    if (kind === 'edit') {
        view.aiClassify = knowledgePointButton('AI 分类', () => { window.openClassifyModal(); window.runAIClassify(); });
        view.aiClassify.setAttribute('data-tooltip', '使用同一分类模型分析教材定位、题型与知识点');
        header.insertBefore(view.aiClassify, view.selected);
    }
    const panel = knowledgePointNode('div', '', 'mb-kp-panel'); view.panel = panel; view.picker.append(panel);
    if (kind !== 'edit') {
        const controls = knowledgePointNode('div', '', 'mb-kp-filter-controls');
        [['knowledge_point_mode', '匹配方式', [['any','任意一个'],['all','同时包含全部']]],
            ['knowledge_point_role', '考查作用', [['main','主要考查'],['all','包括辅助']]]].forEach(([key, title, options]) => {
                const label = knowledgePointNode('label', title); const select = document.createElement('select');
                select.className = 'glass-select'; select.setAttribute('aria-label', title);
                options.forEach(([value, text]) => { const option = knowledgePointNode('option', text); option.value = value; select.append(option); });
                select.addEventListener('change', () => updateKnowledgePointFilter(kind, {[key]: select.value}));
                view[key] = select; label.append(select); controls.append(label);
            });
        const unlabelled = knowledgePointNode('label', '', 'mb-kp-check');
        view.unlabeled = document.createElement('input'); view.unlabeled.type = 'checkbox';
        view.unlabeled.addEventListener('change', () => updateKnowledgePointFilter(kind,
            {knowledge_point_unlabeled: view.unlabeled.checked, ...(view.unlabeled.checked ? {knowledge_point_codes: []} : {})}));
        unlabelled.append(view.unlabeled, knowledgePointNode('span','未标注')); controls.append(unlabelled); view.controls = controls;
    }
    const tools = knowledgePointNode('div', '', 'mb-kp-tools');
    view.current = knowledgePointButton('本节推荐', () => { view.scope = 'current'; view.query = ''; view.page = 0; view.options.scrollTop = 0; renderKnowledgePointPicker(kind); });
    view.global = knowledgePointButton('全库目录', () => { view.scope = 'global'; view.query = ''; view.page = 0; view.options.scrollTop = 0; renderKnowledgePointPicker(kind); });
    view.search = document.createElement('input'); view.search.type = 'search'; view.search.className = 'glass-input';
    view.search.placeholder = '搜索知识点名称，编码用 #1230';
    view.search.setAttribute('aria-label','搜索知识点名称，按编码查找请输入井号加完整编码');
    view.search.addEventListener('input', () => { view.query = view.search.value; view.scope = 'global'; view.page = 0; view.options.scrollTop = 0; renderKnowledgePointOptions(view); });
    tools.append(view.current, view.global, view.search); panel.append(tools);
    view.info = knowledgePointNode('div', '', 'mb-kp-info'); view.options = knowledgePointNode('div', '', 'mb-kp-options');
    view.pager = knowledgePointNode('div', '', 'mb-kp-pager');
    view.previous = knowledgePointButton('上一组', () => { view.page -= 1; view.options.scrollTop = 0; renderKnowledgePointOptions(view); });
    view.pageLabel = knowledgePointNode('span'); view.next = knowledgePointButton('下一组', () => { view.page += 1; view.options.scrollTop = 0; renderKnowledgePointOptions(view); });
    view.pager.append(view.previous, view.pageLabel, view.next); panel.append(view.info, view.options, view.pager);
    if (view.controls) panel.append(view.controls);
    (host.closest('[role="dialog"]') || document.body).append(view.picker);
    host.append(view.menu);
    if (kind === 'edit') {
        view.aiClassify.disabled = Boolean(KnowledgePointState.busy);
        const create = document.createElement('details'); create.className = 'mb-kp-create'; create.append(knowledgePointNode('summary','自定义'));
        create.addEventListener('toggle', () => host.classList.toggle('mb-kp-create-open', create.open));
        const nameLabel = knowledgePointNode('label','知识点名称'); view.createName = document.createElement('input'); view.createName.className = 'glass-input'; nameLabel.append(view.createName);
        const aliasLabel = knowledgePointNode('label','别名（每行一个，可不填）'); view.createAliases = document.createElement('textarea'); view.createAliases.className = 'glass-input'; view.createAliases.rows = 2; aliasLabel.append(view.createAliases);
        const pathLabel = knowledgePointNode('label', '', 'mb-kp-check'); view.createPath = document.createElement('input'); view.createPath.type = 'checkbox';
        pathLabel.append(view.createPath, knowledgePointNode('span','关联当前教材位置（可选）'));
        view.createError = knowledgePointNode('div', '', 'mb-kp-info'); view.createError.setAttribute('role','alert');
        view.createButton = knowledgePointButton('新增并选用', () => createEditorKnowledgePoint(view));
        create.append(nameLabel, aliasLabel, pathLabel, view.createError, view.createButton); host.insertBefore(create, view.menu);
    }
    knowledgePointViews.set(kind, view); renderKnowledgePointPicker(kind);
}
function knowledgePointMatchesQuery(point, query) {
    if (query.startsWith('#')) {
        const code = query.slice(1).trim();
        return [point.code, ...(Array.isArray(point.source_codes) ? point.source_codes : [])]
            .some(value => typeof value === 'string' && value.toLowerCase() === code);
    }
    return [point.name, point.original_name || '', ...(Array.isArray(point.aliases) ? point.aliases : [])]
        .some(value => String(value).toLowerCase().includes(query));
}
// Curriculum paths are recommendations, not selectable parent points. Keep
// their real version and directory order, and discard only redundant parent
// mappings for the same point; a point can still appear in several leaf paths.
function buildKnowledgePointTree(items, version, curriculum = {}, hierarchy = []) {
    const makeBranch = (label, path, order = Number.MAX_SAFE_INTEGER) => ({label, path,
        key: JSON.stringify([version, ...path]), order, children: new Map(), points: new Map()});
    const root = makeBranch('', []);
    const numberOf = label => String(label).match(/^(\d+(?:\.\d+)+)(?:\s|$)/)?.[1] || '';
    const ensure = (parent, label, order = Number.MAX_SAFE_INTEGER) => {
        if (!parent.children.has(label)) parent.children.set(label, makeBranch(label, [...parent.path, label], order));
        return parent.children.get(label);
    };
    const books = Object.keys(curriculum || {});
    const at = (values, value) => { const index = values.indexOf(value); return index < 0 ? Number.MAX_SAFE_INTEGER : index; };
    const sectionHierarchy = new Map((Array.isArray(hierarchy) ? hierarchy : []).map(row =>
        [JSON.stringify([row.compulsory,row.chapter,row.knowledge]),row.ancestors]));
    const ancestorsOf = path => sectionHierarchy.get(JSON.stringify([path.compulsory,path.chapter,path.knowledge]));
    (Array.isArray(items) ? items : []).forEach(point => {
        if (!point || typeof point.code !== 'string' || typeof point.name !== 'string') return;
        const paths = (Array.isArray(point.curriculum_paths) ? point.curriculum_paths : []).filter(path =>
            path && path.version_code === version && typeof path.compulsory === 'string' && path.compulsory
            && typeof path.chapter === 'string' && path.chapter);
        const specific = paths.filter(path => !paths.some(other => other !== path
            && other.compulsory === path.compulsory && other.chapter === path.chapter
            && ((!path.knowledge && other.knowledge)
                || (path.knowledge && path.knowledge === other.knowledge && path.level === 'parent' && other.level === 'leaf')
                || (path.knowledge && ancestorsOf(other)?.includes(path.knowledge)))));
        if (!specific.length) {
            ensure(root, '未关联当前教材').points.set(point.code, point);
            return;
        }
        specific.forEach(path => {
            const chapters = curriculum?.[path.compulsory] || {};
            let branch = ensure(root, path.compulsory, at(books, path.compulsory));
            branch = ensure(branch, path.chapter, at(Object.keys(chapters), path.chapter));
            if (path.knowledge) {
                const sections = Array.isArray(chapters[path.chapter]) ? chapters[path.chapter] : [];
                const ancestors = ancestorsOf(path);
                (ancestors || []).forEach(ancestor => { branch = ensure(branch, ancestor, at(sections, ancestor)); });
                branch = ensure(branch, path.knowledge, at(sections, path.knowledge));
                const sectionNumber = numberOf(path.knowledge);
                const referenceNumber = numberOf(path.reference_section);
                if (!ancestors && path.level === 'leaf' && sectionNumber && referenceNumber.startsWith(sectionNumber + '.')) {
                    branch = ensure(branch, path.reference_section);
                }
            }
            branch.points.set(point.code, point);
        });
    });
    const finalize = branch => {
        const codes = new Set(branch.points.keys());
        const children = [...branch.children.values()].sort((left, right) => left.order - right.order
            || left.label.localeCompare(right.label, 'zh-CN', {numeric: true})).map(child => {
                const result = finalize(child); result.codes.forEach(code => codes.add(code)); return result;
            });
        return {...branch, children, points: [...branch.points.values()], codes, count: codes.size};
    };
    return finalize(root).children;
}
function knowledgePointPathMatchesLocation(path, version, location, hierarchy = []) {
    if (!path || path.version_code !== version || path.compulsory !== location.compulsory || path.chapter !== location.chapter) return false;
    if (!location.knowledge || path.knowledge === location.knowledge) return true;
    return (Array.isArray(hierarchy) ? hierarchy : []).some(row => row.compulsory === path.compulsory
        && row.chapter === path.chapter && row.knowledge === path.knowledge && row.ancestors.includes(location.knowledge));
}
function toggleKnowledgePointSelection(view, point) {
    if (view.kind === 'edit') {
        if (KnowledgePointState.busy) return;
        const selected = KnowledgePointState.snapshot().some(item => item.code === point.code);
        if (selected) KnowledgePointState.remove(point.code); else KnowledgePointState.add({code: point.code, name: point.name});
    } else {
        const selected = KnowledgePointState.filter(view.kind).knowledge_point_codes;
        updateKnowledgePointFilter(view.kind, {knowledge_point_unlabeled: false,
            knowledge_point_codes: selected.includes(point.code) ? selected.filter(code => code !== point.code) : [...selected, point.code]});
    }
}
function knowledgePointOption(view, point, selected, tree = false) {
    const isSelected = selected.includes(point.code);
    const option = tree ? knowledgePointNode('label', '', 'mb-kp-option mb-kp-tree-leaf')
        : knowledgePointButton('', () => toggleKnowledgePointSelection(view, point));
    option.classList.add('mb-kp-option'); option.dataset.kpCode = point.code; option.dataset.code = point.code;
    option.setAttribute('aria-pressed', String(isSelected));
    if (tree) {
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = isSelected;
        checkbox.className = 'mb-kp-tree-checkbox'; checkbox.disabled = view.kind === 'edit' && Boolean(KnowledgePointState.busy);
        checkbox.addEventListener('change', () => {
            const restoreFocus = document.activeElement === checkbox;
            toggleKnowledgePointSelection(view, point);
            if (restoreFocus) {
                const replacement = [...view.options.querySelectorAll('.mb-kp-tree-leaf')].find(node =>
                    node.dataset.kpCode === point.code && node.dataset.treePath === option.dataset.treePath);
                replacement?.querySelector('input')?.focus({preventScroll: true});
            }
        });
        option.append(checkbox);
    } else option.disabled = view.kind === 'edit' && Boolean(KnowledgePointState.busy);
    option.append(knowledgePointNode('span', point.name));
    return option;
}
function renderKnowledgePointTree(view, roots, selected, location) {
    const version = knowledgePointVersion();
    if (!view.treeExpanded.has(version) && roots.length && !view.picker.hidden) {
        const expanded = new Set();
        let branches = roots;
        const hierarchy = knowledgePointCatalogCache.get(version)?.hierarchy || [];
        const ancestors = hierarchy.find(row => row.compulsory === location.compulsory && row.chapter === location.chapter
            && row.knowledge === location.knowledge)?.ancestors || [];
        const wanted = [location.compulsory, location.chapter, ...ancestors, location.knowledge];
        // Open the current path initially; with no location, show a first
        // concrete path so the tree immediately includes selectable leaves.
        for (let depth = 0; branches.length; depth += 1) {
            const branch = branches.find(item => item.label === wanted[depth]) || branches[0];
            expanded.add(branch.key); branches = branch.children;
        }
        view.treeExpanded.set(version, expanded);
    }
    const expanded = view.treeExpanded.get(version) || new Set();
    const list = knowledgePointNode('ul', '', 'mb-kp-tree-list');
    list.setAttribute('aria-label','按当前教材查找知识点');
    const appendBranch = (parent, branch, depth) => {
        const row = knowledgePointNode('li', '', 'mb-kp-tree-branch');
        const toggle = knowledgePointButton('', () => {
            if (expanded.has(branch.key)) expanded.delete(branch.key); else expanded.add(branch.key);
            const scrollTop = view.options.scrollTop;
            renderKnowledgePointOptions(view); view.options.scrollTop = scrollTop;
            const replacement = [...view.options.querySelectorAll('.mb-kp-tree-toggle')].find(node => node.dataset.treeKey === branch.key);
            replacement?.focus({preventScroll: true});
        });
        toggle.classList.add('mb-kp-tree-toggle'); toggle.dataset.treeKey = branch.key;
        toggle.setAttribute('aria-expanded', String(expanded.has(branch.key)));
        toggle.setAttribute('aria-label', `${branch.label}，${branch.count} 个知识点，${expanded.has(branch.key) ? '收起' : '展开'}`);
        toggle.style.setProperty('--mb-kp-tree-depth', String(depth));
        const arrow = knowledgePointNode('i', '', 'fa-solid fa-chevron-right mb-kp-tree-arrow'); arrow.setAttribute('aria-hidden','true');
        toggle.append(arrow, knowledgePointNode('span', branch.label, 'mb-kp-tree-label'),
            knowledgePointNode('span', String(branch.count), 'mb-kp-tree-count'));
        row.append(toggle);
        if (expanded.has(branch.key)) {
            const group = knowledgePointNode('ul', '', 'mb-kp-tree-list');
            branch.children.forEach(child => appendBranch(group, child, depth + 1));
            branch.points.forEach(point => {
                const leaf = knowledgePointNode('li'); const option = knowledgePointOption(view, point, selected, true);
                option.dataset.treePath = branch.key;
                option.style.setProperty('--mb-kp-tree-depth', String(depth + 1)); leaf.append(option); group.append(leaf);
            });
            row.append(group);
        }
        parent.append(row);
    };
    roots.forEach(branch => appendBranch(list, branch, 0)); view.options.append(list);
}
function renderKnowledgePointOptions(view) {
    const entry = knowledgePointCatalogCache.get(knowledgePointVersion());
    const location = currentKnowledgePointLocation(view.kind);
    view.current.disabled = !location.compulsory || !location.chapter;
    if (view.search.value !== view.query) view.search.value = view.query;
    const query = view.query.trim().toLowerCase();
    const recommended = view.scope === 'current' && !query && !view.current.disabled;
    const tree = !recommended && !query;
    view.current.setAttribute('aria-pressed', String(recommended));
    view.global.setAttribute('aria-pressed', String(!recommended));
    const candidates = (entry?.items || []).filter(point => {
        if (query && !knowledgePointMatchesQuery(point, query)) return false;
        return !recommended || (point.curriculum_paths || []).some(path =>
            knowledgePointPathMatchesLocation(path, knowledgePointVersion(), location, entry?.hierarchy));
    });
    const pages = tree ? 1 : Math.max(1, Math.ceil(candidates.length / 6)); view.page = Math.max(0, Math.min(view.page, pages - 1));
    const scrollTop = view.options.scrollTop;
    view.info.replaceChildren(); view.options.replaceChildren();
    view.options.classList.toggle('mb-kp-tree', tree);
    if (entry?.error) view.info.append(knowledgePointNode('span', entry.error), knowledgePointButton('重试', () => loadKnowledgePointCatalog(true).catch(() => {})));
    else view.info.textContent = entry?.promise ? '正在加载知识点目录…' : `${recommended ? '本节关联考点' : query ? '搜索结果' : '全库目录'} · ${candidates.length} 项`;
    const selected = view.kind === 'edit' ? KnowledgePointState.snapshot().map(item => item.code)
        : KnowledgePointState.filter(view.kind).knowledge_point_codes;
    if (tree) renderKnowledgePointTree(view, buildKnowledgePointTree(candidates, knowledgePointVersion(), window.systemMetadata?.curriculum || {}, entry?.hierarchy), selected, location);
    else candidates.slice(view.page * 6, (view.page + 1) * 6).forEach(point => view.options.append(knowledgePointOption(view, point, selected)));
    if (entry?.ready && !candidates.length) view.options.append(knowledgePointNode('span',
        query ? '未找到匹配的知识点。' : recommended ? '本节暂无推荐，可切换全库目录。' : '暂无可选知识点。','mb-kp-info'));
    view.pager.hidden = pages <= 1; view.previous.disabled = view.page === 0; view.next.disabled = view.page === pages - 1;
    view.pageLabel.textContent = `${view.page + 1} / ${pages}`;
    view.options.scrollTop = scrollTop;
    positionKnowledgePointPicker(view);
}
function renderKnowledgePointPicker(kind) {
    const view = knowledgePointViews.get(kind); if (!view?.host.isConnected) return;
    view.selected.replaceChildren(); view.menu.replaceChildren();
    if (kind === 'edit') {
        if (view.aiClassify) view.aiClassify.disabled = Boolean(KnowledgePointState.busy);
        if (view.editorGeneration !== EditorState.generation) {
            view.menuCode = ''; setKnowledgePointPickerOpen(view, false);
            view.editorGeneration = EditorState.generation;
        }
        const labels = KnowledgePointState.snapshot(); labels.forEach(item => { if (item.name) knowledgePointNames.set(item.code,item.name); });
        const accepted = labels;
        accepted.forEach(item => {
            const button = knowledgePointButton(item.name || knowledgePointName(item.code), () => {
                if (KnowledgePointState.busy) return;
                view.menuCode = view.menuCode === item.code ? '' : item.code;
                if (view.menuCode) setKnowledgePointPickerOpen(view, false);
                renderKnowledgePointPicker(kind);
            });
            button.setAttribute('aria-label',(item.name || knowledgePointName(item.code)) + '，' + (item.role === 'auxiliary' ? '辅助涉及' : '主要考查') + '，编辑');
            button.setAttribute('aria-expanded',String(view.menuCode === item.code)); button.disabled = Boolean(KnowledgePointState.busy); view.selected.append(button);
        });
        const menuItem = accepted.find(item => item.code === view.menuCode);
        view.menu.hidden = !menuItem;
        if (menuItem) {
            view.menu.append(knowledgePointNode('span',menuItem.name || knowledgePointName(menuItem.code)));
            const select = document.createElement('select'); select.className = 'glass-select'; select.setAttribute('aria-label','知识点考查作用');
            [['main','主要考查'],['auxiliary','辅助涉及']].forEach(([value,text]) => { const option = knowledgePointNode('option',text); option.value = value; select.append(option); });
            select.value = menuItem.role; select.addEventListener('change', () => { if (!KnowledgePointState.busy) KnowledgePointState.setRole(menuItem.code,select.value); });
            view.menu.append(select, knowledgePointButton('移除', () => { if (!KnowledgePointState.busy) { KnowledgePointState.remove(menuItem.code); view.menuCode = ''; } }),
                knowledgePointButton('完成', () => { view.menuCode = ''; renderKnowledgePointPicker(kind); }));
            const provenance = document.createElement('details'); provenance.className = 'mb-kp-label-details';
            provenance.append(knowledgePointNode('summary','查看来源与依据'));
            const origin = menuItem.origin;
            const research = origin && typeof origin === 'object' && (origin.source === 'ai_research_candidates' || origin.kind === 'reviewed_research_candidate');
            provenance.append(knowledgePointNode('p',research ? 'AI研究标注（已接入，可修改）' : origin === 'manual' ? '手动标注' : origin === 'ai_classification' ? 'AI 自动分类（可修改）' : '已有知识点标注'));
            if (Array.isArray(menuItem.scope)) {
                const scope = menuItem.scope.filter(value => typeof value === 'string' || typeof value === 'number').join('、');
                if (scope) provenance.append(knowledgePointNode('p','范围：' + scope));
            }
            const sourceLabel = new Map([['content','题干'],['analysis','解析'],['answer_markdown','解析'],['review','教师评析'],['review_markdown','教师评析']]).get(menuItem.evidence_source);
            if (typeof menuItem.evidence === 'string' && menuItem.evidence) provenance.append(knowledgePointNode('p',(sourceLabel ? sourceLabel + '原文：' : '原文证据：') + menuItem.evidence));
            if (typeof menuItem.reason === 'string' && menuItem.reason) provenance.append(knowledgePointNode('p','标注理由：' + menuItem.reason));
            view.menu.append(provenance);
        }
        view.createPath.disabled = !currentKnowledgePointLocation('edit').compulsory || !currentKnowledgePointLocation('edit').chapter;
        if (view.createPath.disabled) view.createPath.checked = false;
    } else {
        const f = KnowledgePointState.filter(kind);
        f.knowledge_point_codes.forEach(code => view.selected.append(knowledgePointButton(knowledgePointName(code) + ' ×', () => updateKnowledgePointFilter(kind,
            {knowledge_point_codes: f.knowledge_point_codes.filter(item => item !== code)}))));
        ['knowledge_point_mode','knowledge_point_role'].forEach(key => { view[key].value = f[key]; });
        view.unlabeled.checked = f.knowledge_point_unlabeled;
    }
    view.selected.querySelectorAll('button').forEach(button => { button.dataset.tooltip = button.textContent; });
    if (!view.selected.childNodes.length) view.selected.append(knowledgePointNode('span',
        kind === 'edit' ? '未选择' : KnowledgePointState.filter(kind).knowledge_point_unlabeled ? '未标注' : '不限','mb-kp-info'));
    renderKnowledgePointOptions(view);
}
function renderKnowledgePointViews() {
    knowledgePointViews.forEach((view, kind) => renderKnowledgePointPicker(kind));
    if (typeof window.renderEditorPaperMeta === 'function') window.renderEditorPaperMeta();
    if (typeof window.renderBankKnowledgePointConditions === 'function') window.renderBankKnowledgePointConditions();
}
async function createEditorKnowledgePoint(view) {
    if (view.createButton.disabled || KnowledgePointState.busy) return;
    const name = view.createName.value.trim(); view.createError.textContent = '';
    if (!name) { view.createError.textContent = '请填写知识点名称。'; return; }
    const version = knowledgePointVersion(); const session = EditorState.snapshot();
    const aliases = view.createAliases.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    const existing = (knowledgePointCatalogCache.get(version)?.items || []).find(point =>
        [point.name, ...(point.aliases || [])].some(value => String(value).trim().toLowerCase() === name.toLowerCase()));
    if (existing) { view.createError.textContent = '目录中已存在该名称或别名，请直接选择：' + existing.name; return; }
    const paths = view.createPath.checked ? [{version_code: version, ...currentKnowledgePointLocation('edit')}] : [];
    view.createButton.disabled = true; view.createButton.setAttribute('aria-busy','true');
    try {
        const response = await fetch('/api/knowledge-points', {method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({name, aliases, curriculum_paths: paths, expected_version_code: version})});
        const point = await response.json();
        if (!response.ok || !point || typeof point.code !== 'string') throw new Error(typeof point?.detail === 'string' ? point.detail : '新增知识点失败，请检查名称是否重复。');
        knowledgePointCatalogCache.delete(version); knowledgePointNames.set(point.code,point.name);
        if (EditorState.isCurrent(session) && knowledgePointVersion() === version) {
            KnowledgePointState.add({code:point.code,name:point.name}); view.createName.value = ''; view.createAliases.value = '';
        }
        await loadKnowledgePointCatalog(true);
    } catch (error) { if (EditorState.isCurrent(session)) view.createError.textContent = error.message; }
    finally { view.createButton.disabled = false; view.createButton.removeAttribute('aria-busy'); }
}
function renderKnowledgePointBadges(host, value, f = null, compact = false) {
    if (!host) return;
    const labels = KnowledgePointState.normalize(value); labels.forEach(item => { if (item.name) knowledgePointNames.set(item.code,item.name); });
    const filtered = f?.knowledge_point_codes.length ? KnowledgePointState.matching(labels,f) : labels;
    if (!filtered.length) return;
    const container = knowledgePointNode('div', '', 'mb-kp-badges');
    if (f?.knowledge_point_codes.length) container.append(knowledgePointNode('span','命中考点：','mb-kp-info'));
    (compact ? filtered.slice(0,3) : filtered).forEach(item => container.append(knowledgePointNode('span',
        (item.name || knowledgePointName(item.code)) + ' · ' + (item.role === 'auxiliary' ? '辅助' : '主要'),'mb-kp-badge')));
    if (compact && filtered.length > 3) container.append(knowledgePointNode('span',`+${filtered.length - 3}`,'mb-kp-badge'));
    host.append(container);
}
function renderBankKnowledgePointConditions() {
    const host = document.getElementById('bankKnowledgePointConditions'); if (!host) return;
    host.replaceChildren();
    const f = KnowledgePointState.filter('bank');
    f.knowledge_point_codes.forEach(code => host.append(knowledgePointButton(knowledgePointName(code) + ' ×', () =>
        updateKnowledgePointFilter('bank',{knowledge_point_codes:f.knowledge_point_codes.filter(item => item !== code)}))));
    if (f.knowledge_point_unlabeled) host.append(knowledgePointButton('未标注知识点 ×', () => updateKnowledgePointFilter('bank',{knowledge_point_unlabeled:false})));
    if (f.knowledge_point_codes.length) host.append(knowledgePointNode('span',
        (f.knowledge_point_mode === 'all' ? '同时包含全部' : '包含任意一个') + ' · '
        + (f.knowledge_point_role === 'all' ? '含辅助' : '主要考查'),'mb-kp-info'));
    const filters = document.getElementById('bankFilterFields');
    host.hidden = Boolean(filters?.classList.contains('is-open')) || !host.childNodes.length;
}
window.onEditorKnowledgePointChange = () => {
    renderKnowledgePointPicker('edit');
    if (typeof window.renderEditorPaperMeta === 'function') window.renderEditorPaperMeta();
    if (typeof window.refreshEditorFeedback === 'function') window.refreshEditorFeedback();
};
Object.assign(window,{loadKnowledgePointCatalog, mountKnowledgePointPicker, renderKnowledgePointPicker, renderKnowledgePointViews,
    renderKnowledgePointBadges, knowledgePointName, updateKnowledgePointFilter,
    renderBankKnowledgePointConditions,
    hydrateEditorKnowledgePoints(record) { KnowledgePointState.hydrate(record?.knowledge_points || []); },
    getEditorKnowledgePointsSnapshot() { return KnowledgePointState.snapshot(); }});
document.addEventListener('DOMContentLoaded', () => {
    mountKnowledgePointPicker('edit',document.getElementById('editKnowledgePointPicker'));
    mountKnowledgePointPicker('bank',document.getElementById('bankKnowledgePointPicker'));
});
window.addEventListener('pointerdown', event => {
    knowledgePointViews.forEach(view => {
        if (!view.picker.hidden && !view.picker.contains(event.target) && !view.toggle.contains(event.target)) {
            setKnowledgePointPickerOpen(view, false);
        }
    });
}, true);
window.addEventListener('keydown', event => {
    if (event.key !== 'Escape' && event.key !== 'Esc') return;
    const view = [...knowledgePointViews.values()].find(item => !item.picker.hidden);
    if (!view) return;
    event.preventDefault(); event.stopImmediatePropagation();
    setKnowledgePointPickerOpen(view, false); view.toggle.focus({preventScroll: true});
}, true);
const repositionKnowledgePointPickers = () => knowledgePointViews.forEach(positionKnowledgePointPicker);
window.addEventListener('resize', repositionKnowledgePointPickers);
window.addEventListener('scroll', repositionKnowledgePointPickers, true);
window.visualViewport?.addEventListener('resize', repositionKnowledgePointPickers);
window.visualViewport?.addEventListener('scroll', repositionKnowledgePointPickers);


        function initResizers() {
            const workspace = document.getElementById('bankWorkspaceSection');
            const sidebar = document.getElementById('sidebarSection');
            const preview = document.getElementById('previewSection');
            const resizer1 = document.getElementById('resizer-1');
            if (!workspace || !sidebar || !preview || !resizer1) return;

            const minRatio = Number(resizer1.getAttribute('aria-valuemin')) || 34;
            const maxRatio = Number(resizer1.getAttribute('aria-valuemax')) || 72;
            const defaultRatio = 45;
            let isDragging = false;

            function setBankSplitRatio(value, { notify = false } = {}) {
                const ratio = Math.min(maxRatio, Math.max(minRatio, Number(value) || defaultRatio));
                workspace.style.setProperty('--bank-list-track', `${ratio}fr`);
                workspace.style.setProperty('--bank-detail-track', `${100 - ratio}fr`);
                resizer1.setAttribute('aria-valuenow', String(Math.round(ratio)));
                if (notify) window.dispatchEvent(new Event('resize'));
                return ratio;
            }

            function ratioFromPointer(clientX) {
                const listRect = sidebar.getBoundingClientRect();
                const detailRect = preview.getBoundingClientRect();
                const dividerWidth = resizer1.getBoundingClientRect().width;
                const availableWidth = Math.max(1, detailRect.right - listRect.left - dividerWidth);
                const desiredListWidth = clientX - listRect.left - dividerWidth / 2;
                return desiredListWidth / availableWidth * 100;
            }

            function finishDragging(event) {
                if (!isDragging) return;
                isDragging = false;
                resizer1.classList.remove('is-dragging');
                document.body.style.cursor = '';
                document.body.classList.remove('select-none');
                if (event && resizer1.hasPointerCapture && resizer1.hasPointerCapture(event.pointerId)) {
                    resizer1.releasePointerCapture(event.pointerId);
                }
                window.dispatchEvent(new Event('resize'));
            }

            resizer1.addEventListener('pointerdown', event => {
                if (window.matchMedia('(max-width: 960px)').matches) return;
                event.preventDefault();
                isDragging = true;
                resizer1.classList.add('is-dragging');
                document.body.style.cursor = 'col-resize';
                document.body.classList.add('select-none');
                if (resizer1.setPointerCapture) resizer1.setPointerCapture(event.pointerId);
                setBankSplitRatio(ratioFromPointer(event.clientX));
            });

            resizer1.addEventListener('pointermove', event => {
                if (!isDragging) return;
                event.preventDefault();
                setBankSplitRatio(ratioFromPointer(event.clientX));
            });

            resizer1.addEventListener('pointerup', finishDragging);
            resizer1.addEventListener('pointercancel', finishDragging);

            resizer1.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
                event.preventDefault();
                const currentRatio = Number(resizer1.getAttribute('aria-valuenow')) || defaultRatio;
                const nextRatio = event.key === 'Home'
                    ? defaultRatio
                    : currentRatio + (event.key === 'ArrowLeft' ? -2 : 2);
                setBankSplitRatio(nextRatio, { notify: true });
            });

            resizer1.addEventListener('dblclick', () => {
                setBankSplitRatio(defaultRatio, { notify: true });
            });

            window.setBankSplitRatio = setBankSplitRatio;
        }

        // Copy Original LaTeX content to Clipboard
        function copyPaperContent() {
            const text = document.getElementById('editContent').value;
            if (!text || !text.trim()) {
                showToast('题干内容为空，无法复制！', 'error');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                const btn = document.getElementById('copyContentBtn');
                const originalHTML = btn.innerHTML;
                btn.innerHTML = `<i class="fa-solid fa-check text-green-500"></i><span class="text-[9px] font-bold text-green-500">已复制</span>`;
                showToast('题干 LaTeX 代码已成功复制！', 'success');
                setTimeout(() => {
                    btn.innerHTML = originalHTML;
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy: ', err);
                showToast('复制失败，请手动选择复制。', 'error');
            });
        }

        function copyPaperAnalysis() {
            const text = document.getElementById('editAnswerMarkdown').value;
            if (!text || !text.trim()) {
                showToast('解析内容为空，无法复制！', 'error');
                return;
            }
            navigator.clipboard.writeText(text).then(() => {
                const btn = document.getElementById('copyAnalysisBtn');
                const originalHTML = btn.innerHTML;
                btn.innerHTML = `<i class="fa-solid fa-check text-green-500"></i><span class="text-[9px] font-bold text-green-500">已复制</span>`;
                showToast('答案解析 LaTeX 代码已成功复制！', 'success');
                setTimeout(() => {
                    btn.innerHTML = originalHTML;
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy: ', err);
                showToast('复制失败，请手动选择复制。', 'error');
            });
        }

        // Save callbacks may arrive after the user has typed more text. Accepting
        // an explicit request snapshot keeps those later edits dirty instead of
        // accidentally treating the current DOM as the server-confirmed state.
        function backupEditorState(id = null, draftId = null, requestSnapshot = null) {
            const snapshot = requestSnapshot || {
                content: document.getElementById('editContent').value,
                answer_markdown: document.getElementById('editAnswerMarkdown').value,
                review: document.getElementById('editReview').value,
                question_type: document.getElementById('editQType').value,
                difficulty: document.getElementById('editDifficulty').value,
                source: document.getElementById('editSource').value,
                category_compulsory: document.getElementById('editCompulsory').value,
                category_chapter: document.getElementById('editChapter').value,
                category_knowledge: document.getElementById('editKnowledge').value,
                knowledge_points: KnowledgePointState.snapshot(),
                related_question_id: document.getElementById('editRelatedQuestion').value,
                image_paths: JSON.stringify(uploadedImages),
                tikz_code: TikzState.contentAssets[0] ? TikzState.contentAssets[0].tikz_code : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: JSON.stringify(TikzState.contentAssets),
                answer_tikz_assets: JSON.stringify(TikzState.answerAssets),
                figure_align: FigureLayoutState.align,
                figure_size: FigureLayoutState.size,
                image_layouts: JSON.parse(JSON.stringify(FigureLayoutState.imageLayouts || {})),
                figure_align_custom: FigureLayoutState.customAlign,
                tags: document.getElementById('editTags') ? document.getElementById('editTags').value : ''
            };
            originalQuestionState = {
                id: id,
                draftId: draftId,
                content: snapshot.content,
                answer_markdown: snapshot.answer_markdown,
                review: snapshot.review,
                question_type: snapshot.question_type,
                difficulty: snapshot.difficulty,
                source: snapshot.source,
                category_compulsory: snapshot.category_compulsory,
                category_chapter: snapshot.category_chapter,
                category_knowledge: snapshot.category_knowledge,
                knowledge_points: KnowledgePointState.normalize(snapshot.knowledge_points),
                sub_knowledge: typeof normalizeSubKnowledge === 'function' ? normalizeSubKnowledge(snapshot.sub_knowledge) : (snapshot.sub_knowledge || []),
                sub_knowledge_by_version: requestSnapshot && requestSnapshot.version_code ? {
                    ...(originalQuestionState?.sub_knowledge_by_version || {}),
                    [requestSnapshot.version_code]: snapshot.sub_knowledge || []
                } : (snapshot.sub_knowledge_by_version || {}),
                related_question_id: snapshot.related_question_id || '',
                image_paths: snapshot.image_paths,
                tikz_code: snapshot.tikz_code || '',
                tikz_reference_image_path: snapshot.tikz_reference_image_path || '',
                content_tikz_assets: snapshot.content_tikz_assets || '[]',
                answer_tikz_assets: snapshot.answer_tikz_assets || '[]',
                figure_align: snapshot.figure_align || 'right',
                figure_size: snapshot.figure_size || 'auto',
                image_layouts: snapshot.image_layouts || {},
                figure_align_custom: Boolean(snapshot.figure_align_custom),
                tags: snapshot.tags
            };
            if (typeof refreshEditorFeedback === 'function') refreshEditorFeedback();
        }
        window.backupEditorState = backupEditorState;

        // Association endpoints save independently of the rest of the editor.
        // Advance only this field so concurrent content/answer edits stay dirty.
        function commitEditorRelatedBaseline(session, relatedQuestionId) {
            if (!originalQuestionState || !EditorState.isCurrent(session)
                    || originalQuestionState.id !== session.questionId) {
                return false;
            }
            originalQuestionState.related_question_id = String(relatedQuestionId || '');
            return true;
        }

        function commitEditorFigureLayoutBaseline(
            questionId,
            figureAlign,
            figureSize,
            figureAlignCustom = true
        ) {
            const normalizedId = Number(questionId);
            if (!originalQuestionState || !window.EditorState
                    || EditorState.questionId !== normalizedId
                    || Number(originalQuestionState.id) !== normalizedId) {
                return false;
            }
            if (!['right', 'bottom_left', 'center', 'bottom_right'].includes(figureAlign)) return false;
            if (!['auto', 'small', 'medium', 'large'].includes(figureSize)) return false;
            originalQuestionState.figure_align = figureAlign;
            originalQuestionState.figure_size = figureSize;
            originalQuestionState.figure_align_custom = Boolean(figureAlignCustom);
            return true;
        }
        window.commitEditorFigureLayoutBaseline = commitEditorFigureLayoutBaseline;

        function reconcileEditorFigureLayout(questionId, confirmed, requested, succeeded) {
            const normalizedId = Number(questionId);
            if (!originalQuestionState || !window.EditorState || !window.FigureLayoutState
                    || EditorState.questionId !== normalizedId
                    || Number(originalQuestionState.id) !== normalizedId
                    || typeof FigureLayoutState.snapshot !== 'function') {
                return false;
            }
            const validAlign = value => ['right', 'bottom_left', 'center', 'bottom_right'].includes(value);
            const validSize = value => ['auto', 'small', 'medium', 'large'].includes(value);
            const confirmedAlign = confirmed && confirmed.figure_align;
            const confirmedSize = confirmed && confirmed.figure_size;
            const confirmedCustom = Boolean(confirmed && confirmed.figure_align_custom);
            const requestedAlign = requested && requested.figure_align;
            const requestedSize = requested && requested.figure_size;
            const requestedCustom = Boolean(requested && requested.figure_align_custom);
            if (!validAlign(confirmedAlign) || !validSize(confirmedSize)
                    || !validAlign(requestedAlign) || !validSize(requestedSize)) {
                return false;
            }

            const current = FigureLayoutState.snapshot();
            const baselineAlign = originalQuestionState.figure_align || 'right';
            const baselineSize = originalQuestionState.figure_size || 'auto';
            const baselineCustom = Boolean(originalQuestionState.figure_align_custom);
            const wasClean = current.figure_align === baselineAlign
                && current.figure_size === baselineSize
                && Boolean(current.figure_align_custom) === baselineCustom;
            const matchesRequested = current.figure_align === requestedAlign
                && current.figure_size === requestedSize
                && Boolean(current.figure_align_custom) === requestedCustom;

            if (succeeded) {
                originalQuestionState.figure_align = confirmedAlign;
                originalQuestionState.figure_size = confirmedSize;
                originalQuestionState.figure_align_custom = confirmedCustom;
                if (wasClean || matchesRequested) {
                    FigureLayoutState.setAlign(confirmedAlign);
                    FigureLayoutState.setSize(confirmedSize);
                    FigureLayoutState.setCustomAlign(confirmedCustom);
                }
            } else if (matchesRequested) {
                FigureLayoutState.setAlign(baselineAlign);
                FigureLayoutState.setSize(baselineSize);
                FigureLayoutState.setCustomAlign(baselineCustom);
            }
            return true;
        }
        window.reconcileEditorFigureLayout = reconcileEditorFigureLayout;

        function editorMatchesBackupSnapshot(snapshot) {
            if (!snapshot) return false;
            const currentContent = document.getElementById('editContent').value;
            const currentAnswer = document.getElementById('editAnswerMarkdown').value;
            const currentReview = document.getElementById('editReview').value;
            const currentType = document.getElementById('editQType').value;
            const currentDifficulty = document.getElementById('editDifficulty').value;
            const currentSource = document.getElementById('editSource').value;
            const currentComp = document.getElementById('editCompulsory').value;
            const currentChap = document.getElementById('editChapter').value;
            const currentKnow = document.getElementById('editKnowledge').value;
            const currentRelatedQuestionId = document.getElementById('editRelatedQuestion').value;
            const currentImages = JSON.stringify(uploadedImages);
            const currentTikzCode = TikzState.contentAssets[0]
                ? TikzState.contentAssets[0].tikz_code
                : '';
            const currentTikzReferencePath = TikzState.contentAssets[0]
                ? (TikzState.contentAssets[0].reference_image_path || '')
                : '';
            const currentContentTikzAssets = JSON.stringify(TikzState.contentAssets);
            const currentAnswerTikzAssets = JSON.stringify(TikzState.answerAssets);
            const currentFigureAlign = FigureLayoutState.align;
            const currentFigureSize = FigureLayoutState.size;
            const currentFigureAlignCustom = FigureLayoutState.customAlign;
            const currentTags = document.getElementById('editTags') ? document.getElementById('editTags').value : '';

            return currentContent === snapshot.content &&
                   currentAnswer === snapshot.answer_markdown &&
                   currentReview === snapshot.review &&
                   currentType === snapshot.question_type &&
                   currentDifficulty === snapshot.difficulty &&
                   currentSource === snapshot.source &&
                   currentComp === snapshot.category_compulsory &&
                   currentChap === snapshot.category_chapter &&
                   currentKnow === snapshot.category_knowledge &&
                   KnowledgePointState.signature(KnowledgePointState.snapshot()) === KnowledgePointState.signature(snapshot.knowledge_points) &&
                   (typeof subKnowledgeSignature !== 'function' || subKnowledgeSignature(getEditorSubKnowledge()) === subKnowledgeSignature(snapshot.sub_knowledge)) &&
                   (typeof subKnowledgeVersionsSignature !== 'function' || !snapshot.sub_knowledge_by_version || subKnowledgeVersionsSignature(editorSubKnowledgeVersionSnapshot()) === subKnowledgeVersionsSignature(snapshot.sub_knowledge_by_version)) &&
                   currentRelatedQuestionId === (snapshot.related_question_id || '') &&
                   currentImages === snapshot.image_paths &&
                   currentTikzCode === (snapshot.tikz_code || '') &&
                   currentTikzReferencePath === (snapshot.tikz_reference_image_path || '') &&
                   currentContentTikzAssets === (snapshot.content_tikz_assets || '[]') &&
                   currentAnswerTikzAssets === (snapshot.answer_tikz_assets || '[]') &&
                   currentFigureAlign === (snapshot.figure_align || 'right') &&
                   JSON.stringify(FigureLayoutState.imageLayouts || {}) === JSON.stringify(snapshot.image_layouts || {}) &&
                   currentFigureSize === (snapshot.figure_size || 'auto') &&
                   currentFigureAlignCustom === Boolean(snapshot.figure_align_custom) &&
                   currentTags === snapshot.tags;
        }
        window.editorMatchesBackupSnapshot = editorMatchesBackupSnapshot;

        // Helper to check if the current question has been modified from its original loaded state
        function isEditorModified() {
            if (!originalQuestionState) return false;
            return !editorMatchesBackupSnapshot(originalQuestionState);
        }
        window.isEditorModified = isEditorModified;

        // Custom Premium Confirmation Modal for Unsaved Changes (3 Options)
        function showUnsavedChangesModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'unsavedChangesModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl p-6 space-y-4 transform scale-100 transition-all border border-slate-100">
                        <div class="flex items-center space-x-2 pb-2 border-b">
                            <i class="fa-solid fa-circle-question text-brand-600 text-base animate-pulse"></i>
                            <h3 id="unsavedChangesModalTitle" class="font-bold text-sm text-slate-800">当前编辑内容有未保存的修改</h3>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            您刚才编辑的题目尚未存入正式题库。请选择您希望如何处理这些修改？
                        </p>
                        <div class="flex flex-col space-y-2 pt-2">
                            <button id="saveToBankBtn" type="button" class="w-full px-4 py-2 bg-brand-600/80 hover:bg-brand-600 text-white rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 backdrop-blur-sm border border-brand-500/20 shadow-sm">
                                <i class="fa-solid fa-cloud-arrow-up"></i>
                                <span>存入本地库 (正式题库)</span>
                            </button>
                            <button id="saveToDraftsBtn" type="button" class="w-full px-4 py-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>暂存至草稿箱</span>
                            </button>
                            <button id="discardBtn" type="button" class="w-full px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>直接离开 (不保存)</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t">
                            <button id="cancelBtn" type="button" class="px-4 py-1.5 border border-slate-300 rounded-xl text-slate-600 hover:bg-slate-50 transition-all text-[10px] font-medium">
                                返回编辑
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                const finish = (result) => {
                    window.MathBankModal.close(modalDiv);
                    if (modalDiv.isConnected) modalDiv.remove();
                    resolve(result);
                };
                window.MathBankModal.open(modalDiv, { onEscape: () => finish('cancel') });

                document.getElementById('saveToBankBtn').onclick = () => {
                    finish('bank');
                };

                document.getElementById('saveToDraftsBtn').onclick = () => {
                    finish('drafts');
                };

                document.getElementById('discardBtn').onclick = () => {
                    finish('discard');
                };

                document.getElementById('cancelBtn').onclick = () => {
                    finish('cancel');
                };
            });
        }

        // Custom Premium Confirmation Modal for Missing School Phase (Compulsory)
        function showMissingCompulsoryModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none opacity-0 transition-opacity duration-300";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'missingClassificationModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4 transform scale-95 transition-all duration-300 border border-slate-100/55">
                        <div class="flex items-center space-x-2.5 pb-2.5 border-b border-slate-100">
                            <div class="w-8 h-8 rounded-xl bg-brand-50 flex items-center justify-center">
                                <i class="fa-solid fa-wand-magic-sparkles text-brand-600 text-sm animate-pulse"></i>
                            </div>
                            <div>
                                <h3 id="missingClassificationModalTitle" class="font-bold text-sm text-slate-800">题目分类信息不完整</h3>
                                <p class="text-[10px] text-slate-400">MATHBANK 教研分类指引</p>
                            </div>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            为了确保题目能够被精准定位和检索，每道题都需要分配<strong>学段（如：必修一）</strong>与<strong>章节</strong>。您可以选择：
                        </p>
                        <div class="flex flex-col space-y-2 pt-1">
                            <button id="manualCompulsoryBtn" type="button" class="w-full px-4 py-2.5 bg-slate-50 hover:bg-slate-100 active:scale-[0.99] text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-2 border border-slate-200/50">
                                <i class="fa-solid fa-pen-to-square text-slate-500"></i>
                                <span>手动选择 / 输入教材定位</span>
                            </button>
                            <button id="autoSaveClassifyBtn" type="button" class="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 active:scale-[0.99] text-white rounded-xl font-bold transition-all text-xs flex items-center justify-center space-x-2 shadow-sm">
                                <i class="fa-solid fa-wand-magic-sparkles"></i>
                                <span>交给系统自动分析并定位</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t border-slate-100">
                            <button id="cancelCompulsoryBtn" type="button" class="px-4 py-2 border border-slate-200 rounded-xl text-slate-500 hover:bg-slate-50 transition-all text-[11px] font-medium active:scale-[0.98]">
                                取消保存
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                // Add fade-in transition
                setTimeout(() => {
                    modalDiv.classList.remove('opacity-0');
                    modalDiv.querySelector('div').classList.remove('scale-95');
                    modalDiv.querySelector('div').classList.add('scale-100');
                }, 50);

                const closeModal = (result) => {
                    window.MathBankModal.close(modalDiv);
                    modalDiv.classList.add('opacity-0');
                    modalDiv.querySelector('div').classList.remove('scale-100');
                    modalDiv.querySelector('div').classList.add('scale-95');
                    setTimeout(() => {
                        document.body.removeChild(modalDiv);
                        resolve(result);
                    }, 300);
                };
                window.MathBankModal.open(modalDiv, { onEscape: () => closeModal('cancel') });

                document.getElementById('manualCompulsoryBtn').onclick = () => closeModal('manual');
                document.getElementById('autoSaveClassifyBtn').onclick = () => closeModal('ai');
                document.getElementById('cancelCompulsoryBtn').onclick = () => closeModal('cancel');
            });
        }

        // Check if editor has unsaved changes, show modal if needed, then run callback
        async function checkAndSwitch(actionCallback) {
            if (window.isQuestionSaveInFlight && window.isQuestionSaveInFlight()) {
                showToast('题目正在保存，请等待完成后再切换', 'info');
                return;
            }
            if (isEditorModified()) {
                const choice = await showUnsavedChangesModal();
                
                if (choice === 'bank') {
                    // Try to save to SQLite database
                    const saveSuccess = await saveQuestion();
                    if (saveSuccess) {
                        actionCallback();
                    }
                } else if (choice === 'drafts') {
                    // Save to Drafts Box in LocalStorage
                    saveCurrentToDrafts();
                    actionCallback();
                } else if (choice === 'discard') {
                    // Directly leave
                    actionCallback();
                } else {
                    // 'cancel' -> do nothing!
                }
            } else {
                actionCallback();
            }
        }


        function refreshEditorFeedback() {
            const status = document.getElementById('editorSaveStatus');
            const hint = document.getElementById('editorValidationHint');
            if (!status || !hint) return;
            const missing = [];
            const modal = document.getElementById('editorSection');
            [['editContent', '题干'], ['editCompulsory', '学段'], ['editChapter', '章节']].forEach(([id, label]) => {
                const input = document.getElementById(id);
                if (!input) return;
                const empty = !input.value.trim();
                if (empty) missing.push(label);
                input.setAttribute('aria-invalid', empty && modal.dataset.validationAttempted === 'true' ? 'true' : 'false');
            });
            status.textContent = typeof isQuestionSaveInFlight === 'function' && isQuestionSaveInFlight()
                ? '正在保存…' : isEditorModified() ? '有未保存的修改' : EditorState.questionId ? '已保存' : '新题目 · 尚未入库';
            hint.textContent = missing.length ? `保存前请补充：${missing.join('、')}。带 * 的项目为必填项。` : '必填信息已齐全，可以保存。';
            hint.classList.toggle('is-complete', missing.length === 0);
        }
        window.refreshEditorFeedback = refreshEditorFeedback;
        function renderBankFilterConditions(selected, subdivisions, open) {
            const host = document.getElementById('bankFilterConditions');
            if (!host) return;
            host.replaceChildren();
            function appendCondition(title, remove) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'bank-filter-condition';
                button.setAttribute('aria-label', '移除筛选条件 ' + title);
                button.setAttribute('data-tooltip', title);
                const label = document.createElement('span');
                label.className = 'bank-filter-condition-text';
                label.textContent = title;
                const close = document.createElement('span');
                close.className = 'bank-filter-condition-remove';
                close.textContent = '×';
                close.setAttribute('aria-hidden', 'true');
                button.append(label, close);
                button.addEventListener('click', remove);
                host.append(button);
            }
            selected.forEach(input => {
                const title = input.tagName === 'SELECT' ? input.selectedOptions[0]?.textContent || input.value : input.value;
                appendCondition(title, () => {
                    input.value = '';
                    if (input.id === 'filterSource') {
                        document.getElementById('clearFilterSourceBtn')?.classList.add('hidden');
                        reloadBankKnowledgePointFilters();
                    } else input.dispatchEvent(new Event('change', {bubbles: true}));
                });
            });
            subdivisions.forEach(path => appendCondition(subKnowledgePathLabel(path), () => {
                const signature = JSON.stringify(path);
                renderSubKnowledgeOptions('filter', getFilterSubKnowledge().filter(item => JSON.stringify(item) !== signature));
                reloadBankKnowledgePointFilters();
            }));
            host.hidden = open || !host.childNodes.length;
        }
        function updateBankFilterSummary() {
            const button = document.querySelector('.bank-filter-toggle');
            const clear = document.getElementById('clearBankFiltersBtn');
            if (!button || !clear) return;
            const selected = ['filterCompulsory', 'filterChapter', 'filterKnowledge', 'filterType', 'filterDifficulty', 'filterSource']
                .map(id => document.getElementById(id)).filter(element => element && element.value);
            const subdivisions = getFilterSubKnowledge();
            const pointFilter = KnowledgePointState.filter('bank');
            const count = selected.length + (subdivisions.length ? 1 : 0)
                + (pointFilter.knowledge_point_codes.length || pointFilter.knowledge_point_unlabeled ? 1 : 0);
            const open = Boolean(document.getElementById('bankFilterFields')?.classList.contains('is-open'));
            button.textContent = count ? `筛选 (${count})` : '筛选';
            button.setAttribute('data-tooltip', [...selected.map(element => element.tagName === 'SELECT' ? element.selectedOptions[0]?.textContent : element.value), ...subdivisions.map(subKnowledgePathLabel)].join(' / ') || '按学段、章节、小节、细分知识点等条件筛选');
            clear.hidden = count === 0;
            renderBankFilterConditions(selected, subdivisions, open);
            renderBankKnowledgePointConditions();
            document.getElementById('bankWorkspaceSection')?.classList.toggle('bank-has-filter-summary', !open && count > 0);
        }
        function clearBankFilters() {
            KnowledgePointState.setFilter('bank', {knowledge_point_codes: [], knowledge_point_mode:'any',
                knowledge_point_role:'main', knowledge_point_status:'all', knowledge_point_unlabeled:false});
            renderKnowledgePointPicker('bank');
            ['filterCompulsory', 'filterChapter', 'filterKnowledge', 'filterType', 'filterDifficulty', 'filterSource'].forEach(id => {
                const element = document.getElementById(id);
                if (element) element.value = '';
            });
            document.getElementById('clearFilterSourceBtn')?.classList.add('hidden');
            renderSubKnowledgeOptions('filter', []);
            const mode = document.getElementById('filterSubKnowledgeMode');
            if (mode) mode.value = 'any';
            const reload = document.getElementById('filterCompulsory')?.onchange;
            if (typeof reload === 'function') reload();
            else {
                currentBankPage = currentDraftPage = 1;
                if (activeSidebarTab === 'bank') loadQuestions(); else loadDrafts();
            }
        }
        window.clearBankFilters = clearBankFilters;
        function toggleBankFilters(button) {
            const fields = document.getElementById('bankFilterFields');
            const open = fields.classList.toggle('is-open');
            button.setAttribute('aria-expanded', String(open));
            document.getElementById('bankWorkspaceSection').classList.toggle('bank-filters-open', open);
            updateBankFilterSummary();
        }
        function showBankDetail() {
            if (!window.matchMedia('(max-width: 960px)').matches || (window.PaperStore && window.PaperStore.activeWorkspace !== 'bank')) return;
            document.getElementById('bankWorkspaceSection').classList.add('bank-detail-open');
            const back = document.querySelector('.bank-detail-back');
            if (back) back.focus({preventScroll: true});
        }
        function closeBankDetail() {
            document.getElementById('bankWorkspaceSection').classList.remove('bank-detail-open');
        }
        window.toggleBankFilters = toggleBankFilters;
        window.showBankDetail = showBankDetail;
        window.closeBankDetail = closeBankDetail;
        document.addEventListener('input', event => {
            if (event.target.closest && event.target.closest('#editorSection')) refreshEditorFeedback();
        });
        document.addEventListener('change', event => {
            if (event.target.closest && event.target.closest('#editorSection')) refreshEditorFeedback();
        });

        let questionEditorRestoreFocus = null;

        function switchQuestionEditorPanel(panelId) {
            const validPanels = ['classification', 'content', 'answer'];
            const targetPanel = validPanels.includes(panelId) ? panelId : 'classification';
            document.getElementById('editorSection').dataset.panel = targetPanel;

            document.querySelectorAll('[data-editor-panel]').forEach(panel => {
                const isActive = panel.dataset.editorPanel === targetPanel;
                panel.classList.toggle('active', isActive);
                panel.classList.toggle('hidden', !isActive);
                panel.setAttribute('aria-hidden', isActive ? 'false' : 'true');
            });

            document.querySelectorAll('[data-editor-panel-target]').forEach(button => {
                const isActive = button.dataset.editorPanelTarget === targetPanel;
                button.classList.toggle('active', isActive);
                if (isActive) {
                    button.setAttribute('aria-current', 'step');
                } else {
                    button.removeAttribute('aria-current');
                }
            });

            const body = document.querySelector('.question-editor-modal-body');
            if (body) body.scrollTop = 0;
        }

        function setQuestionEditorBackgroundInert(isInert) {
            document.querySelectorAll(
                '#appNavigation, #appContentShell > header, #bankWorkspaceSection > :not(#editorSection)'
            ).forEach(element => {
                element.inert = isInert;
                if (isInert) {
                    element.setAttribute('aria-hidden', 'true');
                } else {
                    element.removeAttribute('aria-hidden');
                }
            });
        }

        function setQuestionDetailEditAvailability(isAvailable) {
            const button = document.getElementById('editQuestionFromPreviewBtn');
            if (!button) return;
            button.disabled = !isAvailable;
            button.setAttribute('aria-disabled', isAvailable ? 'false' : 'true');
        }

        function openQuestionEditorModal(panelId = 'content') {
            const modal = document.getElementById('editorSection');
            const editButton = document.getElementById('editQuestionFromPreviewBtn');
            if (!modal) return;
            if (editButton && editButton.disabled && !modal.dataset.allowNewQuestion) {
                showToast('请先从题库中选择一道题目', 'info');
                return;
            }

            questionEditorRestoreFocus = document.activeElement;
            switchQuestionEditorPanel(panelId);
            if (panelId === 'content' && typeof switchContentTab === 'function') {
                switchContentTab('ocr');
            }
            modal.classList.remove('hidden');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('question-editor-open');
            setQuestionEditorBackgroundInert(true);

            requestAnimationFrame(() => {
                const activeStep = modal.querySelector('[data-editor-panel-target].active');
                const title = document.getElementById('editorTitle');
                if (title) { title.tabIndex = -1; title.focus({ preventScroll: true }); }
            });
            delete modal.dataset.allowNewQuestion;
            delete modal.dataset.validationAttempted;
            refreshEditorFeedback();
        }

        function finishCloseQuestionEditorModal() {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('question-editor-open');
            setQuestionEditorBackgroundInert(false);
            if (questionEditorRestoreFocus && questionEditorRestoreFocus.isConnected) {
                questionEditorRestoreFocus.focus({ preventScroll: true });
            }
            questionEditorRestoreFocus = null;
        }

        function restoreEditorAfterDiscard() {
            if (!originalQuestionState) return;
            if (originalQuestionState.id && typeof selectQuestion === 'function') {
                selectQuestion({ id: originalQuestionState.id }, { silent: true });
                return;
            }
            if (originalQuestionState.draftId) {
                const draft = getLocalStorageDrafts().find(item => item.id === originalQuestionState.draftId);
                if (draft) selectDraft(draft);
                return;
            }
            if (typeof startNewQuestionWithoutPrompt === 'function') {
                startNewQuestionWithoutPrompt();
            }
        }

        function closeQuestionEditorModal() {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            const shouldRestore = isEditorModified();
            const isNewQuestionMode = modal.dataset.newQuestionMode === 'true';
            const returnQuestionId = Number(modal.dataset.returnQuestionId || 0);
            checkAndSwitch(() => {
                finishCloseQuestionEditorModal();
                if (isNewQuestionMode && Number.isSafeInteger(returnQuestionId) && returnQuestionId > 0) {
                    selectQuestion({ id: returnQuestionId }, { silent: true });
                } else if (shouldRestore) {
                    restoreEditorAfterDiscard();
                }
                delete modal.dataset.newQuestionMode;
                delete modal.dataset.returnQuestionId;
            });
        }

        function openNewQuestionEditor() {
            checkAndSwitch(() => {
                const returnQuestionId = Number(EditorState.questionId || 0);
                // The list refresh performed by startNewQuestion must not
                // auto-select the first question again and overwrite the new
                // question draft while its modal is opening.
                window.__preserveNewQuestionEditor = true;
                startNewQuestion();
                const title = document.getElementById('editorTitle');
                if (title) title.textContent = '录入新数学题';
                setQuestionDetailEditAvailability(false);
                const modal = document.getElementById('editorSection');
                if (modal) {
                    modal.dataset.allowNewQuestion = 'true';
                    modal.dataset.newQuestionMode = 'true';
                    if (Number.isSafeInteger(returnQuestionId) && returnQuestionId > 0) {
                        modal.dataset.returnQuestionId = String(returnQuestionId);
                    }
                }
                openQuestionEditorModal();
                setQuestionDetailEditAvailability(false);
            });
        }

        document.addEventListener('keydown', event => {
            const modal = document.getElementById('editorSection');
            if (!modal || modal.classList.contains('hidden')) return;
            if (event.key === 'Escape') {
                event.preventDefault();
                closeQuestionEditorModal();
                return;
            }
            if (event.key !== 'Tab') return;
            const focusable = Array.from(modal.querySelectorAll(
                'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter(element => element.getClientRects().length > 0);
            if (focusable.length === 0) return;
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });

        window.switchQuestionEditorPanel = switchQuestionEditorPanel;
        window.openQuestionEditorModal = openQuestionEditorModal;
        window.closeQuestionEditorModal = closeQuestionEditorModal;
        window.openNewQuestionEditor = openNewQuestionEditor;
        window.setQuestionDetailEditAvailability = setQuestionDetailEditAvailability;

        // ==========================================
        //         LOCALSTORAGE DRAFTS SYSTEM
        // ==========================================

        function getLocalStorageDrafts() {
            try {
                return JSON.parse(localStorage.getItem('mathbank_local_drafts')) || [];
            } catch(e) {
                return [];
            }
        }

        function setLocalStorageDrafts(drafts) {
            localStorage.setItem('mathbank_local_drafts', JSON.stringify(drafts));
        }

        function updateDraftCountBadge() {
            const drafts = getLocalStorageDrafts();
            const badge = document.getElementById('draftCount');
            if (badge) {
                badge.textContent = drafts.length;
            }
        }

        function saveCurrentToDrafts() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return false;
            }
            if (typeof window.syncEditorImageReferences === 'function') window.syncEditorImageReferences();
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const compulsory = document.getElementById('editCompulsory').value;
            const chapter = document.getElementById('editChapter').value;
            const knowledge = document.getElementById('editKnowledge').value;
            const difficulty = document.getElementById('editDifficulty').value;
            const source = document.getElementById('editSource').value;
            const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
            const review = document.getElementById('editReview').value;
            const tags = document.getElementById('editTags') ? document.getElementById('editTags').value.trim() : '';
            const previousDraft = getLocalStorageDrafts().find(item => item.id === EditorState.draftId) || {};
            
            const draft = {
                id: EditorState.draftId || ('draft-' + Date.now()),
                content: content,
                question_type: qtype,
                category_compulsory: compulsory,
                category_chapter: chapter,
                category_knowledge: knowledge,
                knowledge_points: KnowledgePointState.snapshot(),
                sub_knowledge: getEditorSubKnowledge(),
                version_code: window.activeCurriculumVersion || '',
                sub_knowledge_by_version: {
                    ...(previousDraft.sub_knowledge_by_version || {}),
                    ...editorSubKnowledgeVersionSnapshot()
                },
                classification_by_version: {...(previousDraft.classification_by_version || {}), ...editorClassificationVersionSnapshot()},
                saved_question_id: EditorState.questionId || previousDraft.saved_question_id || null,
                saved_sub_knowledge_by_version: previousDraft.saved_sub_knowledge_by_version || originalQuestionState?.sub_knowledge_by_version || {},
                difficulty: difficulty,
                source: source,
                answer_markdown: answerMarkdown,
                review: review,
                tags: tags,
                image_paths: typeof window.editorAssetReferences === 'function'
                    ? window.editorAssetReferences() : Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : []),
                    ...TikzState.referencePaths()
                ])),
                tikz_code: TikzState.contentAssets[0]
                    ? TikzState.contentAssets[0].tikz_code
                    : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: TikzState.contentAssets,
                answer_tikz_assets: TikzState.answerAssets,
                figure_align: FigureLayoutState.align,
                figure_size: FigureLayoutState.size,
                image_layouts: JSON.parse(JSON.stringify(FigureLayoutState.imageLayouts || {})),
                figure_align_custom: FigureLayoutState.customAlign,
                isDraft: true,
                updated_at: new Date().toISOString()
            };
            
            let drafts = getLocalStorageDrafts();
            const index = drafts.findIndex(d => d.id === draft.id);
            if (index > -1) {
                drafts[index] = draft;
            } else {
                drafts.unshift(draft);
            }
            
            setLocalStorageDrafts(drafts);
            EditorState.setDraftId(draft.id);
            
            // Backup the new draft state as the "original state" so the editor is no longer modified
            backupEditorState(EditorState.questionId, draft.id);
            
            updateDraftCountBadge();
            showToast('已暂存至草稿箱！');
            
            // Reload drafts if active
            if (activeSidebarTab === 'drafts') {
                loadDrafts();
            }
        }

        function selectDraft(draft) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            if (draft.saved_question_id) {
                EditorState.useQuestion({id: draft.saved_question_id});
                EditorState.setDraftId(draft.id);
            } else EditorState.useDraft(draft);
            
            // Populate form fields
            document.getElementById('editContent').value = draft.content || '';
            setEditorMetadataValue(document.getElementById('editQType'), draft.question_type || 'single_choice');
            setEditorMetadataValue(document.getElementById('editDifficulty'), draft.difficulty || 'easy_error');
            document.getElementById('editSource').value = draft.source || '';
            document.getElementById('editAnswerMarkdown').value = draft.answer_markdown || '';
            document.getElementById('editReview').value = draft.review || '';
            if (document.getElementById('editTags')) {
                document.getElementById('editTags').value = draft.tags || '';
            }
            
            // Load cascading categories
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            
            // Reset dropdowns
            compSelect.value = '';
            compSelect.onchange();
            
            const classification = draftClassification(draft);
            if (classification.category_compulsory) {
                setEditorMetadataValue(compSelect, classification.category_compulsory);
                compSelect.onchange();
                if (classification.category_chapter) {
                    setEditorMetadataValue(chapSelect, classification.category_chapter);
                    chapSelect.onchange();
                    if (classification.category_knowledge) {
                        setEditorMetadataValue(knowSelect, classification.category_knowledge);
                    }
                }
            }
            hydrateEditorSubKnowledge(draft, true);
            KnowledgePointState.hydrate(draft.knowledge_points || []);
            
            // Load images
            const allDraftImages = Array.isArray(draft.image_paths)
                ? draft.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            uploadedAnswerImages = typeof window.collectAnswerImagePaths === 'function'
                ? window.collectAnswerImagePaths(draft.answer_markdown || '')
                : [];
            uploadedImages = window.MathBankImageAssets.withoutReviewOnly(
                allDraftImages, draft.content, draft.answer_markdown, draft.review
            ).filter(path => !uploadedAnswerImages.includes(path));
            window.hydrateTikzState(draft);
            FigureLayoutState.hydrate(draft);
            const hiddenTikzReferencePaths = new Set(TikzState.referencePaths());
            uploadedImages = uploadedImages.filter(path => !hiddenTikzReferencePaths.has(path));
            renderIllustrationBadges();
            if (typeof window.renderAnswerImageBadges === 'function') {
                window.renderAnswerImageBadges();
            }
            if (typeof window.renderAnswerTikzAssets === 'function') {
                window.renderAnswerTikzAssets();
            }
            if (typeof window.renderContentTikzAssets === 'function') {
                window.renderContentTikzAssets();
            }
            
            // Update preview and side panels
            if (typeof window.updateContentPreview === 'function') {
                window.updateContentPreview();
            } else {
                document.getElementById('editContent').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateAnswerPreview === 'function') {
                window.updateAnswerPreview();
            } else {
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateReviewPreview === 'function') {
                window.updateReviewPreview();
            } else {
                document.getElementById('editReview').dispatchEvent(new Event('input'));
            }
            renderEditorPaperMeta();
            
            document.getElementById('editorTitle').textContent = `编辑草稿 - 暂存中`;
            
            // Restore one stable association target without downloading the bank.
            if (typeof refreshRelatedDropdown === 'function') {
                refreshRelatedDropdown(draft.related_question_id || '', {expectedValue: ''});
            }

            // Backup draft state
            backupEditorState(EditorState.questionId, draft.id);
            setQuestionDetailEditAvailability(true);
            
            // Active highlighting in sidebar drafts list
            if (activeSidebarTab === 'drafts') {
                highlightActiveDraftCard(draft.id);
            }
        }

        function highlightActiveDraftCard(id) {
            const cards = document.querySelectorAll('#questionsList > div');
            cards.forEach(c => {
                if (c.getAttribute('data-draft-id') === id) {
                    c.className = "p-3.5 mx-1.5 rounded-xl border glass-card bg-white cursor-pointer transition-all duration-200 shadow-md ring-2 ring-emerald-100 border-emerald-500 flex flex-col space-y-2 select-none group relative";
                } else {
                    c.className = "p-3.5 mx-1.5 rounded-xl border glass-card hover:bg-white cursor-pointer transition-all duration-200 shadow-sm flex flex-col space-y-2 select-none group relative border-slate-200";
                }
            });
        }

        function loadDrafts() {
            cancelPendingSidebarSearch();
            invalidatePendingBankQuestions();
            if (typeof updateBankFilterSummary === 'function') updateBankFilterSummary();
            const qListContainer = document.getElementById('questionsList');
            const q = document.getElementById('searchInput').value.trim().toLowerCase();
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const compulsory = document.getElementById('filterCompulsory').value;
            const chapter = document.getElementById('filterChapter').value;
            const knowledge = document.getElementById('filterKnowledge')?.value || '';
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value.trim().toLowerCase() : '';
            
            let drafts = getLocalStorageDrafts();
            
            // Filter by type
            if (qtype) {
                drafts = drafts.filter(item => item.question_type === qtype);
            }
            
            // Filter by difficulty
            if (difficulty) {
                drafts = drafts.filter(item => item.difficulty === difficulty);
            }
            
            // One complete path must satisfy the cascade; never combine fields
            // from the original location and a different subdivision.
            drafts = drafts.filter(item => {
                const classification = draftClassification(item);
                return matchesQuestionCurriculumFilters({...item,
                    category_compulsory: classification.category_compulsory || '',
                    category_chapter: classification.category_chapter || '',
                    category_knowledge: classification.category_knowledge || ''},
                    {compulsory, chapter, knowledge}, subKnowledgeForDraft(item));
            });
            drafts = drafts.filter(item => KnowledgePointState.matches(item.knowledge_points || [], KnowledgePointState.filter('bank')));

            // Filter by source
            if (source) {
                drafts = drafts.filter(item => (item.source || '').toLowerCase().includes(source));
            }
            
            // Search filter for drafts
            if (q) {
                drafts = drafts.filter(item => {
                    return (item.content || '').toLowerCase().includes(q) ||
                           (item.source || '').toLowerCase().includes(q) ||
                           (item.category_chapter || '').toLowerCase().includes(q) ||
                           (item.review || '').toLowerCase().includes(q) ||
                           (item.tags || '').toLowerCase().includes(q);
                });
            }
            
            // Sort Drafts by time (updated_at)
            const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
            drafts.sort((a, b) => {
                let dateA = a.updated_at ? new Date(a.updated_at).getTime() : 0;
                let dateB = b.updated_at ? new Date(b.updated_at).getTime() : 0;
                
                if (!dateA && a.id && String(a.id).startsWith('draft-')) {
                    const parts = String(a.id).split('-');
                    if (parts.length > 1) {
                        dateA = parseInt(parts[1], 10) || 0;
                    }
                }
                if (!dateB && b.id && String(b.id).startsWith('draft-')) {
                    const parts = String(b.id).split('-');
                    if (parts.length > 1) {
                        dateB = parseInt(parts[1], 10) || 0;
                    }
                }
                
                if (dateA !== dateB) {
                    return sortOrder === 'asc' ? dateA - dateB : dateB - dateA;
                }
                return sortOrder === 'asc' ? String(a.id).localeCompare(String(b.id)) : String(b.id).localeCompare(String(a.id));
            });

            const totalItems = drafts.length;
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            if (currentDraftPage > totalPages) {
                currentDraftPage = totalPages;
            }
            if (currentDraftPage < 1) {
                currentDraftPage = 1;
            }
            
            qListContainer.innerHTML = '';
            
            if (totalItems === 0) {
                qListContainer.innerHTML = `
                    <div class="ui-state ui-state-empty bank-list-state">
                        <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-box-open"></i></span>
                        <strong class="ui-state-title">草稿箱空空如也</strong>
                        <span class="ui-state-description">编辑题目时保存的草稿会显示在这里。</span>
                    </div>`;
                renderSidebarPagination(0, 1, 'drafts');
                return;
            }
            
            const pageItems = drafts.slice((currentDraftPage - 1) * PAGE_LIMIT, currentDraftPage * PAGE_LIMIT);
            
            pageItems.forEach(item => {
                const difficultyBadge = getDifficultyBadge(item.difficulty);
                const typeText = getTypeText(item.question_type);
                
                const itemCard = document.createElement('div');
                itemCard.setAttribute('data-draft-id', item.id);
                
                const isActive = EditorState.draftId === item.id;
                itemCard.className = `p-3.5 mx-1.5 rounded-xl border glass-card hover:bg-white cursor-pointer transition-all duration-200 shadow-sm flex flex-col space-y-2 select-none group relative ${isActive ? 'border-emerald-500 bg-white ring-2 ring-emerald-100 shadow-md' : 'border-slate-200'}`;
                
                const cleanContent = parseMarkdownWithMath(item.content || '', item.image_layouts || {});
                
                let tagsHtml = '';
                if (item.tags) {
                    const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                    if (tagList.length > 0) {
                        const displayTags = tagList.slice(0, 2);
                        const hiddenCount = tagList.length - 2;
                        
                        displayTags.forEach(tag => {
                            tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${window.MathBankSafe.escapeText(tag)}</span></span>`;
                        });
                        
                        if (hiddenCount > 0) {
                            const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${window.MathBankSafe.escapeText(tag)}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
                            tagsHtml += `
                            <div class="relative flex items-center" onclick="event.stopPropagation()">
                                <span class="peer text-[9px] font-bold text-amber-600 bg-amber-100 border border-amber-300/60 px-1.5 py-0.5 rounded-full cursor-default flex items-center shadow-sm hover:bg-amber-200 transition-colors">+${hiddenCount}</span>
                                <div class="absolute top-full right-0 mt-1.5 w-max max-w-[220px] bg-amber-50 border border-amber-200/80 text-amber-800 text-[10px] px-2.5 py-1.5 rounded-lg shadow-md opacity-0 pointer-events-none peer-hover:opacity-100 transition-opacity duration-150 z-50 font-medium invisible peer-hover:visible">
                                    <div class="flex flex-wrap items-center leading-relaxed">
                                        ${fullTagsHtml}
                                    </div>
                                </div>
                            </div>`;
                        }
                    }
                }

                itemCard.innerHTML = `
                    <div class="flex items-start justify-between">
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 shrink-0 mt-0.5">草稿 • ${window.MathBankSafe.escapeText(typeText)}</span>
                        <div class="flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                            ${tagsHtml}
                            ${difficultyBadge}
                            <!-- Delete Button -->
                            <button type="button" aria-label="删除草稿" class="delete-draft-btn text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除草稿">
                                <i class="fa-solid fa-trash-can text-[10px]"></i>
                            </button>
                        </div>
                    </div>
                    <div class="text-xs text-slate-700 leading-relaxed font-medium ${cleanContent.includes('choices-grid') ? 'question-choice-preview' : 'line-clamp-2'} card-formula-render">${cleanContent || '[未填题干]'}</div>
                    <div class="flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                        <span class="truncate max-w-[120px] font-semibold text-emerald-600"><i class="fa-solid fa-box mr-0.5"></i>${window.MathBankSafe.escapeText(item.category_knowledge || item.category_chapter || '未分类')}</span>
                        <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '草稿暂存')}</span>
                    </div>
                `;

                const deleteButton = itemCard.querySelector('.delete-draft-btn');
                if (deleteButton) {
                    deleteButton.addEventListener('click', (event) => {
                        event.stopPropagation();
                        deleteDraft(item.id);
                    });
                }
                
                // Render KaTeX inline for this card
                try {
                    renderMathInElement(itemCard.querySelector('.card-formula-render'), {
                        delimiters: [
                            {left: '$$', right: '$$', display: false},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: false}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    console.error('KaTeX sidebar rendering error: ', e);
                }
                
                itemCard.onclick = () => {
                    checkAndSwitch(() => selectDraft(item));
                };
                
                qListContainer.appendChild(itemCard);
                adaptChoicesGridLayout(itemCard);
            });
            
            renderSidebarPagination(totalItems, currentDraftPage, 'drafts');
        }

        function deleteDraft(id) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认要删除这篇草稿吗？')) {
                let drafts = getLocalStorageDrafts();
                drafts = drafts.filter(d => d.id !== id);
                setLocalStorageDrafts(drafts);
                
                showToast('草稿已删除！');
                updateDraftCountBadge();
                
                if (EditorState.draftId === id) {
                    // Reset current draft state
                    EditorState.clearDraft();
                    startNewQuestionWithoutPrompt();
                }
                
                if (activeSidebarTab === 'drafts') {
                    loadDrafts();
                }
            }
        }

        function openStatsModal() {
            const modal = document.getElementById('statsModal');
            const triggerButton = document.getElementById('statsOpenBtn');
            if (triggerButton && triggerButton.disabled) return;
            const triggerButtonContent = triggerButton ? triggerButton.innerHTML : '';
            if (triggerButton) {
                triggerButton.disabled = true;
                triggerButton.setAttribute('aria-busy', 'true');
                triggerButton.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><span>加载统计</span>';
            }
            
            // 🟢 先拉取并渲染数据，让弹窗内部 DOM 完全静态就绪后再显示弹窗，完美消除毛玻璃背景下的二次重绘闪烁冲突
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        globalStatsData = data;
                        
                        // Render total counters
                        document.getElementById('statsTotalCount').textContent = data.total_count;
                        document.getElementById('statsNormalCount').textContent = data.normal_count;
                        document.getElementById('statsEasyErrorCount').textContent = data.easy_error_count;
                        document.getElementById('statsChallengeCount').textContent = data.challenge_count;
                        document.getElementById('statsQiangjiCount').textContent = data.qiangji_count;
                        
                        // Populate compulsory stages for stats query
                        populateStatsQueryCompulsory();
                        
                        // Set current local Year and Month
                        const now = new Date();
                        document.getElementById('statsYearSelect').value = now.getFullYear().toString();
                        document.getElementById('statsMonthSelect').value = (now.getMonth() + 1).toString();
                        
                        // Render increments calendar
                        renderStatsCalendar();
                        
                        // Reset query selections
                        document.getElementById('statsQueryCompulsory').value = '';
                        const chapSelect = document.getElementById('statsQueryChapter');
                        chapSelect.innerHTML = '<option value="">-- 先选择学段 --</option>';
                        chapSelect.disabled = true;
                        document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
                        document.getElementById('statsQueryResultData').classList.add('hidden');
                        
                        // 数据和图表完全就绪，再顺滑滑入弹窗并淡化背景
                        document.body.classList.add('modal-active');
                        modal.classList.remove('hidden');
                        window.MathBankModal.open(modal, { onEscape: closeStatsModal });
                        setTimeout(() => {
                            modal.classList.remove('opacity-0');
                            modal.querySelector('div').classList.remove('scale-95');
                            modal.querySelector('div').classList.add('scale-100');
                        }, 50);
                    } else {
                        showToast('获取统计大屏数据失败: ' + data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('请求统计数据出错: ' + err, 'error');
                })
                .finally(() => {
                    if (!triggerButton) return;
                    triggerButton.disabled = false;
                    triggerButton.removeAttribute('aria-busy');
                    triggerButton.innerHTML = triggerButtonContent;
                });
        }

        function closeStatsModal() {
            const modal = document.getElementById('statsModal');
            window.MathBankModal.close(modal);
            document.body.classList.remove('modal-active');
            
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function populateStatsQueryCompulsory() {
            const compSelect = document.getElementById('statsQueryCompulsory');
            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            if (globalStatsData && globalStatsData.compulsory_chapter_counts) {
                Object.keys(globalStatsData.compulsory_chapter_counts).forEach(comp => {
                    compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(comp)}">${window.MathBankSafe.escapeText(comp)}</option>`;
                });
            }
        }

        function onStatsQueryCompulsoryChange() {
            const compVal = document.getElementById('statsQueryCompulsory').value;
            const chapSelect = document.getElementById('statsQueryChapter');
            
            chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
            document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
            document.getElementById('statsQueryResultData').classList.add('hidden');
            
            if (compVal && globalStatsData && globalStatsData.compulsory_chapter_counts[compVal]) {
                chapSelect.disabled = false;
                Object.keys(globalStatsData.compulsory_chapter_counts[compVal]).forEach(chap => {
                    chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(chap)}">${window.MathBankSafe.escapeText(chap)}</option>`;
                });
            } else {
                chapSelect.disabled = true;
            }
        }

        async function onStatsQueryChapterChange() {
            const compVal = document.getElementById('statsQueryCompulsory').value;
            const chapVal = document.getElementById('statsQueryChapter').value;
            
            const emptyPanel = document.getElementById('statsQueryResultEmpty');
            const dataPanel = document.getElementById('statsQueryResultData');
            
            if (!compVal || !chapVal) {
                emptyPanel.classList.remove('hidden');
                dataPanel.classList.add('hidden');
                return;
            }
            
            emptyPanel.classList.add('hidden');
            dataPanel.classList.remove('hidden');
            
            // Get count for selected chapter
            const count = globalStatsData.compulsory_chapter_counts[compVal][chapVal] || 0;
            document.getElementById('statsQueryCount').textContent = count;
            
            // Query local questions list to get knowledge point distributions
            const params = new URLSearchParams();
            params.append('compulsory', compVal);
            params.append('chapter', chapVal);
            
            const listContainer = document.getElementById('statsQueryKnowledgeList');
            listContainer.innerHTML = '<div class="text-[10px] text-slate-400 py-4 text-center"><i class="fa-solid fa-spinner animate-spin mr-1"></i>正在计算知识点分布...</div>';
            
            try {
                const response = await fetch(`/api/questions?${params.toString()}`);
                const questions = await response.json();
                
                // Group by knowledge
                const knowStats = {};
                questions.forEach(q => {
                    const know = q.category_knowledge || '未细分知识点';
                    knowStats[know] = (knowStats[know] || 0) + 1;
                });
                
                listContainer.innerHTML = '';
                if (Object.keys(knowStats).length === 0) {
                    listContainer.innerHTML = '<div class="text-[10px] text-slate-500 text-center py-4">本章暂无细分知识点</div>';
                } else {
                    Object.entries(knowStats).forEach(([know, knCount]) => {
                        const pct = Math.round((knCount / count) * 100);
                        listContainer.innerHTML += `
                            <div class="space-y-1 bg-slate-50/70 dark:bg-slate-800/50 p-2 rounded-lg border border-slate-100 dark:border-slate-700/60">
                                <div class="flex justify-between items-center text-[10px] font-semibold text-slate-700 dark:text-slate-200">
                                    <span class="truncate pr-2">${window.MathBankSafe.escapeText(know)}</span>
                                    <span class="font-mono text-slate-600 dark:text-slate-400 text-[10px]">${knCount} 题 (${pct}%)</span>
                                </div>
                                <div class="w-full bg-slate-200 dark:bg-slate-700 h-1.5 rounded-full overflow-hidden">
                                    <div class="bg-brand-500 h-1.5 rounded-full" style="width: ${pct}%"></div>
                                </div>
                            </div>
                        `;
                    });
                }
            } catch (err) {
                listContainer.innerHTML = '<div class="text-[10px] text-red-500 py-4 text-center">加载失败</div>';
            }
        }

        function renderStatsCalendar() {
            const year = parseInt(document.getElementById('statsYearSelect').value);
            const month = parseInt(document.getElementById('statsMonthSelect').value);
            const grid = document.getElementById('statsCalendarGrid');
            
            grid.innerHTML = '';
            
            // Get first day of month (0 = Sunday, 6 = Saturday)
            const firstDayIndex = new Date(year, month - 1, 1).getDay();
            // Get total days in month
            const daysInMonth = new Date(year, month, 0).getDate();
            
            // Pre-fill empty days for previous month alignment
            for (let i = 0; i < firstDayIndex; i++) {
                const emptyCell = document.createElement('div');
                emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                grid.appendChild(emptyCell);
            }
            
            // Daily additions data
            const dailyAdds = (globalStatsData && globalStatsData.daily_adds) ? globalStatsData.daily_adds : {};
            
            // Generate cells
            for (let day = 1; day <= daysInMonth; day++) {
                const dayCell = document.createElement('div');
                
                // Format YYYY-MM-DD
                const mStr = String(month).padStart(2, '0');
                const dStr = String(day).padStart(2, '0');
                const dateStr = `${year}-${mStr}-${dStr}`;
                
                const count = dailyAdds[dateStr] || 0;
                
                const isToday = (new Date().getFullYear() === year && new Date().getMonth() + 1 === month && new Date().getDate() === day);
                
                if (count > 0) {
                    dayCell.className = `p-1 bg-rose-50/80 dark:bg-rose-500/15 border border-rose-200/60 dark:border-rose-500/30 hover:bg-rose-100/80 dark:hover:bg-rose-500/25 rounded-lg flex flex-col justify-between items-center transition-all shadow-sm cursor-help select-none ${isToday ? 'ring-2 ring-rose-400 dark:ring-rose-400' : ''}`;
                    dayCell.title = `当天最终录入：${count} 道题目`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-bold text-rose-800 dark:text-rose-200 ${isToday ? 'bg-rose-200 dark:bg-rose-500/30 px-1 py-0.5 rounded-md' : ''}">${day}</span>
                        <span class="text-[10px] font-extrabold text-rose-600 dark:text-rose-400 font-mono animate-[bounce_1.5s_infinite]">+${count}</span>
                    `;
                } else {
                    dayCell.className = `p-1 bg-white dark:bg-slate-900/50 border border-slate-200/40 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/60 rounded-lg flex flex-col justify-start items-center transition-all select-none ${isToday ? 'ring-2 ring-brand-500 border-brand-200 dark:ring-brand-500' : ''}`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-medium text-slate-600 dark:text-slate-300 ${isToday ? 'bg-brand-100 dark:bg-brand-900/60 text-brand-700 dark:text-brand-200 px-1 py-0.5 rounded-md font-bold' : ''}">${day}</span>
                    `;
                }
                
                grid.appendChild(dayCell);
            }
            
            // Fill remaining grid spaces to keep calendar layout perfect
            const totalCellsUsed = firstDayIndex + daysInMonth;
            const remainingCells = 42 - totalCellsUsed;
            if (remainingCells > 0 && remainingCells < 7) {
                const limit = totalCellsUsed <= 35 ? 35 : 42;
                const pad = limit - totalCellsUsed;
                for (let i = 0; i < pad; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            } else {
                for (let i = 0; i < remainingCells; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            }
        }

        function populateCategoryDropdowns() {
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            
            if (!compSelect || !chapSelect || !knowSelect) return;
            if (!categoryTree || typeof categoryTree !== 'object') {
                console.warn('[Security Shield] 分类数据未准备完毕，跳过编辑区分类级联填充');
                return;
            }
            
            // Backup selection values to prevent losing them during async reloads
            const selectedComp = compSelect.value;
            const selectedChap = chapSelect.value;
            const selectedKnow = knowSelect.value;
            
            // 1. Compulsory
            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            Object.keys(categoryTree).forEach(c => {
                compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(c)}">${window.MathBankSafe.escapeText(c)}</option>`;
            });
            
            compSelect.onchange = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;
                
                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(ch)}">${window.MathBankSafe.escapeText(ch)}</option>`;
                    });
                } else {
                    chapSelect.disabled = true;
                }
                renderKnowledgePointPicker('edit');
            };
            
            chapSelect.onchange = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (可不选，默认整章) --</option>';
                
                if (comp && chap && Array.isArray(categoryTree[comp]?.[chap])) {
                    knowSelect.disabled = false;
                    categoryTree[comp][chap].forEach(k => {
                        knowSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(k)}">${window.MathBankSafe.escapeText(k)}</option>`;
                    });
                } else {
                    knowSelect.disabled = !(comp && chap);
                }
                renderKnowledgePointPicker('edit');
            };

            // Retain historical classifications exactly, including a chapter
            // name stored as knowledge, even when absent from today's directory.
            if (selectedComp) {
                setEditorMetadataValue(compSelect, selectedComp);
                compSelect.onchange();
                if (selectedChap) {
                    setEditorMetadataValue(chapSelect, selectedChap);
                    chapSelect.disabled = false;
                    chapSelect.onchange();
                    if (selectedKnow) setEditorMetadataValue(knowSelect, selectedKnow);
                }
            }
            renderSubKnowledgeOptions('edit');
            knowSelect.onchange = () => renderKnowledgePointPicker('edit');
            renderKnowledgePointPicker('edit');
        }

        // Populate Categories in Filters
        function populateFilterDropdowns() {
            const compSelect = document.getElementById('filterCompulsory');
            const chapSelect = document.getElementById('filterChapter');
            const knowSelect = document.getElementById('filterKnowledge');
            if (!compSelect || !chapSelect || !knowSelect || !categoryTree || typeof categoryTree !== 'object') return;
            const previous = [compSelect.value, chapSelect.value, knowSelect.value];
            function fill(select, values, placeholder, selected = '') {
                select.innerHTML = '<option value="">' + placeholder + '</option>' + values.map(value =>
                    `<option value="${window.MathBankSafe.escapeAttribute(value)}">${window.MathBankSafe.escapeText(value)}</option>`).join('');
                select.value = values.includes(selected) ? selected : '';
            }
            function fillKnowledge(selected = '') {
                const values = categoryTree[compSelect.value]?.[chapSelect.value];
                fill(knowSelect, Array.isArray(values) ? values : [], chapSelect.value ? '所有小节' : '先选择章节', selected);
                knowSelect.disabled = !Array.isArray(values);
                knowSelect.classList.remove('hidden');
            }
            function fillChapters(chapter = '', knowledge = '') {
                const chapters = categoryTree[compSelect.value];
                fill(chapSelect, chapters ? Object.keys(chapters) : [], chapters ? '所有章节' : '先选择学段', chapter);
                chapSelect.disabled = !chapters;
                chapSelect.classList.remove('hidden');
                fillKnowledge(knowledge);
            }
            function reload() {
                currentBankPage = 1;
                currentDraftPage = 1;
                updateBankFilterSummary();
                renderKnowledgePointPicker('bank');
                if (activeSidebarTab === 'bank') loadQuestions();
                else loadDrafts();
            }
            fill(compSelect, Object.keys(categoryTree), '所有学段', previous[0]);
            fillChapters(previous[1], previous[2]);
            compSelect.onchange = () => { fillChapters(); renderSubKnowledgeOptions('filter', []); reload(); };
            chapSelect.onchange = () => { fillKnowledge(); renderSubKnowledgeOptions('filter', []); reload(); };
            knowSelect.onchange = () => { renderSubKnowledgeOptions('filter', []); reload(); };
            const mode = document.getElementById('filterSubKnowledgeMode');
            if (mode) mode.onchange = reload;
            renderSubKnowledgeOptions('filter');
            renderKnowledgePointPicker('bank');
            updateBankFilterSummary();
        }

        // Load and List Saved Questions
        function loadQuestions(retryCount = 0) {
            // Explicit filters, paging and refreshes supersede a queued keyword.
            cancelPendingSidebarSearch();
            if (typeof updateBankFilterSummary === 'function') updateBankFilterSummary();
            const loadSequence = ++bankQuestionsLoadSequence;
            if (bankQuestionsRetryTimer) {
                clearTimeout(bankQuestionsRetryTimer);
                bankQuestionsRetryTimer = null;
            }
            if (bankQuestionsLoadController) {
                bankQuestionsLoadController.abort();
            }
            const requestController = typeof AbortController !== 'undefined'
                ? new AbortController()
                : null;
            bankQuestionsLoadController = requestController;

            const q = document.getElementById('searchInput').value;
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const compulsory = document.getElementById('filterCompulsory').value;
            const chapter = document.getElementById('filterChapter').value;
            const knowledge = document.getElementById('filterKnowledge')?.value || '';
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value : '';
            const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
            const requestedPage = Math.max(1, currentBankPage);
            
            const params = new URLSearchParams();
            if (q) params.append('q', q);
            if (qtype) params.append('qtype', qtype);
            if (difficulty) params.append('difficulty', difficulty);
            if (compulsory) params.append('compulsory', compulsory);
            if (chapter) params.append('chapter', chapter);
            if (knowledge) params.append('knowledge', knowledge);
            KnowledgePointState.appendParams(params, KnowledgePointState.filter('bank'));
            if (source) params.append('source', source);
            params.append('page', String(requestedPage));
            params.append('page_size', String(PAGE_LIMIT));
            params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc');
            
            const qListContainer = document.getElementById('questionsList');
            if (!qListContainer) return;
            qListContainer.setAttribute('aria-busy', 'true');
            const fetchOptions = requestController ? { signal: requestController.signal } : undefined;
            
            fetch(`/api/questions?${params.toString()}`, fetchOptions)
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(payload => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    if (!payload || !Array.isArray(payload.items)) {
                        throw new Error('题库分页响应格式无效');
                    }

                    const questions = payload.items;
                    const parsedTotal = Number(payload.total);
                    const totalItems = Number.isFinite(parsedTotal) && parsedTotal >= 0 ? parsedTotal : 0;
                    const parsedTotalPages = Number(payload.total_pages);
                    const totalPages = Number.isFinite(parsedTotalPages) && parsedTotalPages > 0
                        ? parsedTotalPages
                        : Math.max(1, Math.ceil(totalItems / PAGE_LIMIT));
                    const parsedResponsePage = Number(payload.page);
                    const responsePage = Number.isFinite(parsedResponsePage) && parsedResponsePage > 0
                        ? parsedResponsePage
                        : requestedPage;

                    currentBankPage = Math.min(responsePage, totalPages);
                    qListContainer.innerHTML = '';
                    const shouldAutoSelectFirstQuestion = !window.__preserveNewQuestionEditor &&
                        !EditorState.questionId &&
                        questions.length > 0 && !isEditorModified();
                    
                    if (totalItems === 0) {
                        qListContainer.innerHTML = `
                            <div class="ui-state ui-state-empty bank-list-state">
                                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-box-open"></i></span>
                                <strong class="ui-state-title">未找到匹配题目</strong>
                                <span class="ui-state-description">请调整搜索词或筛选条件后重试。</span>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        return;
                    }
                    
                    questions.forEach(item => {
                        // Create card element
                        const difficultyBadge = getDifficultyBadge(item.difficulty);
                        const typeText = getTypeText(item.question_type);
                        
                        const itemCard = document.createElement('div');
                        itemCard.className = `question-card bank-question-card p-3.5 mx-1.5 flex flex-col space-y-2 select-none group relative ${EditorState.questionId === item.id ? 'active' : ''}`;
                        itemCard.dataset.id = item.id;
                        
                        const cleanContent = parseMarkdownWithMath(item.content || '', item.image_layouts || {});
                        
                        let tagsHtml = '';
                        if (item.tags) {
                            const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                            if (tagList.length > 0) {
                                const displayTags = tagList.slice(0, 2);
                                const hiddenCount = tagList.length - 2;
                                
                                displayTags.forEach(tag => {
                                    tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${window.MathBankSafe.escapeText(tag)}</span></span>`;
                                });
                                
                                if (hiddenCount > 0) {
                                    const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${window.MathBankSafe.escapeText(tag)}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
                                    tagsHtml += `
                                    <div class="relative flex items-center" onclick="event.stopPropagation()">
                                        <span class="peer text-[9px] font-bold text-amber-600 bg-amber-100 border border-amber-300/60 px-1.5 py-0.5 rounded-full cursor-default flex items-center shadow-sm hover:bg-amber-200 transition-colors">+${hiddenCount}</span>
                                        <div class="absolute top-full right-0 mt-1.5 w-max max-w-[220px] bg-amber-50 border border-amber-200/80 text-amber-800 text-[10px] px-2.5 py-1.5 rounded-lg shadow-md opacity-0 pointer-events-none peer-hover:opacity-100 transition-opacity duration-150 z-50 font-medium invisible peer-hover:visible">
                                            <div class="flex flex-wrap items-center leading-relaxed">
                                                ${fullTagsHtml}
                                            </div>
                                        </div>
                                    </div>`;
                                }
                            }
                        }

                        itemCard.innerHTML = `
                            <div class="bank-question-card-header flex items-start justify-between">
                                <span class="bank-question-type text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0 mt-0.5">${window.MathBankSafe.escapeText(typeText)}</span>
                                <div class="bank-question-badges flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                                    <div class="bank-question-tags">${tagsHtml}</div>
                                    ${difficultyBadge}
                                    <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded bg-brand-50 text-brand-600 shadow-sm">#${window.MathBankSafe.escapeText(item.seq_num)}</span>
                                    <!-- Delete Button -->
                                    <button type="button" aria-label="删除题目" onclick="event.stopPropagation(); deleteQuestion(${item.id})" class="text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除">
                                        <i class="fa-solid fa-trash-can text-[10px]"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="bank-question-excerpt text-xs text-slate-700 leading-relaxed font-medium ${cleanContent.includes('choices-grid') ? 'question-choice-preview' : 'line-clamp-2'} card-formula-render">${cleanContent || '[空白题干]'}</div>
                            <div class="bank-question-meta flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                                <span class="truncate max-w-[120px] font-semibold"><i class="fa-solid fa-folder-open mr-0.5"></i>${window.MathBankSafe.escapeText(item.category_knowledge || item.category_chapter || '未分类')}</span>
                                <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '本地录入')}</span>
                                <time class="bank-question-time" title="录入于 ${window.MathBankSafe.escapeText(formatChineseDate(item.created_at))}">${window.MathBankSafe.escapeText(String(item.created_at || '').slice(0, 10))}</time>
                            </div>
                        `;
                        
                        renderKnowledgePointBadges(itemCard, item.knowledge_points || [], KnowledgePointState.filter('bank'), true);
                        // Render KaTeX inline for this card
                        try {
                            renderMathInElement(itemCard.querySelector('.card-formula-render'), {
                                delimiters: [
                                    {left: '$$', right: '$$', display: false},
                                    {left: '$', right: '$', display: false},
                                    {left: '\\(', right: '\\)', display: false},
                                    {left: '\\[', right: '\\]', display: false}
                                ],
                                throwOnError: false
                            });
                        } catch(e) {
                            console.error('KaTeX sidebar rendering error: ', e);
                        }
                        
                        itemCard.onclick = () => {
                            checkAndSwitch(() => selectQuestion(item));
                        };
                        
                        qListContainer.appendChild(itemCard);
                        adaptChoicesGridLayout(itemCard);
                    });

                    if (shouldAutoSelectFirstQuestion) {
                        // Initial list hydration is background work, not a
                        // user selection; avoid showing a success toast on
                        // every page refresh while keeping the first preview.
                        selectQuestion(questions[0], { silent: true });
                    }
                    
                    renderSidebarPagination(totalItems, currentBankPage, 'bank');
                })
                .catch(err => {
                    if ((err && err.name === 'AbortError') || loadSequence !== bankQuestionsLoadSequence) {
                        return;
                    }
                    console.error('加载题库列表发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载题库数据...`);
                        bankQuestionsRetryTimer = setTimeout(() => {
                            if (loadSequence === bankQuestionsLoadSequence) {
                                loadQuestions(retryCount + 1);
                            }
                        }, 1500);
                    } else {
                        qListContainer.innerHTML = `
                            <div class="ui-state ui-state-error bank-list-state" role="alert">
                                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-triangle-exclamation"></i></span>
                                <strong class="ui-state-title">获取题库列表失败</strong>
                                <span class="ui-state-description">后台服务正在启动或连接超时</span>
                                <button onclick="loadQuestions()" class="ui-state-action inline-flex items-center space-x-1 cursor-pointer">
                                    <i class="fa-solid fa-arrows-rotate"></i><span>重新加载</span>
                                </button>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        showToast('系统正在连接或初始化后台，加载题库失败，请稍后刷新重试', 'error');
                    }
                })
                .finally(() => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    if (window.__preserveNewQuestionEditor) {
                        window.__preserveNewQuestionEditor = false;
                    }
                    qListContainer.removeAttribute('aria-busy');
                    if (bankQuestionsLoadController === requestController) {
                        bankQuestionsLoadController = null;
                    }
                });
        }

        // ==========================================
        //       SIDEBAR PAGINATION SYSTEM HELPERS
        // ==========================================
        function renderSidebarPagination(totalItems, currentPage, tabType) {
            const container = document.getElementById('sidebarPagination');
            if (!container) return;
            const summary = document.getElementById('questionResultSummary');
            if (summary) {
                summary.textContent = `共 ${totalItems} 道题`;
            }
            
            if (totalItems === 0) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.style.display = 'flex';
            
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            
            if (totalPages <= 1) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.innerHTML = `
                <div class="sidebar-pagination-controls flex items-center justify-between">
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === 1 ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage - 1}, '${tabType}')">上一页</button>
                    <label class="pagination-page-label">第 <input type="number" aria-label="跳转页码" min="1" max="${totalPages}" value="${currentPage}" class="pagination-jump-input" onkeydown="if(event.key==='Enter') jumpToSidebarPage(this.value, ${totalPages}, '${tabType}')"> / ${totalPages} 页</label>
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage + 1}, '${tabType}')">下一页</button>
                </div>`;
        }

        function goToSidebarPage(page, tabType) {
            if (tabType === 'bank') {
                currentBankPage = page;
                loadQuestions();
            } else {
                currentDraftPage = page;
                loadDrafts();
            }
            // Scroll questionsList back to top gently
            const qListContainer = document.getElementById('questionsList');
            if (qListContainer) {
                qListContainer.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }
        
        function jumpToSidebarPage(value, maxPage, tabType) {
            let page = parseInt(value, 10);
            if (isNaN(page)) return;
            if (page < 1) page = 1;
            if (page > maxPage) page = maxPage;
            goToSidebarPage(page, tabType);
        }

        // Expose to global scope for inline onclick handlers
        window.renderSidebarPagination = renderSidebarPagination;
        window.goToSidebarPage = goToSidebarPage;
        window.jumpToSidebarPage = jumpToSidebarPage;

        // Format ISO Date string to Chinese local datetime: xxxx年xx月xx日xx时xx分
        function escapeEditorMetaText(value) {
            return window.MathBankSafe.escapeText(value);
        }

        function renderEditorPaperMeta() {
            const badges = document.getElementById('paperBadges');
            const sourceEl = document.getElementById('paperFooterSource');
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            if (!badges || !sourceEl || !editQType || !editDifficulty || !editSource) {
                return;
            }

            const seqBadge = EditorState.seqNum != null
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">编号：#${escapeEditorMetaText(EditorState.seqNum)}</span>`
                : '<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-200 text-slate-500">编号：新题目</span>';

            const createdAtBadge = EditorState.createdAt
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 inline-flex items-center"><i class="fa-regular fa-clock mr-1"></i>${escapeEditorMetaText(String(EditorState.createdAt).slice(0, 10))}</span>`
                : '';

            let paperTagsHtml = '';
            const tagsVal = editTags ? editTags.value.trim() : '';
            if (tagsVal) {
                const tagList = tagsVal.split(/[,，]+/).map(tag => tag.trim()).filter(tag => tag.length > 0);
                tagList.forEach(tag => {
                    paperTagsHtml += `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-amber-50 text-amber-600 border border-amber-300/60 flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[8px] text-amber-500 mr-1"></i>${escapeEditorMetaText(tag)}</span>`;
                });
            }

            const diffColor = (typeof getDifficultyColor === 'function')
                ? getDifficultyColor(editDifficulty.value)
                : 'bg-indigo-50 text-indigo-700';

            badges.innerHTML = `
                ${seqBadge}
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-brand-50 text-brand-700">题型：${escapeEditorMetaText(getTypeText(editQType.value))}</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md ${diffColor}">难度：${escapeEditorMetaText(getDifficultyText(editDifficulty.value))}</span>
                ${createdAtBadge}
                ${paperTagsHtml}
            `;
            renderKnowledgePointBadges(badges, KnowledgePointState.snapshot(), KnowledgePointState.filter('bank'));
            sourceEl.textContent = `来源: ${editSource.value || '本地教研录入'}`;
        }
        window.renderEditorPaperMeta = renderEditorPaperMeta;

        const choicesGridSizes = new WeakMap();
        const choicesGridObserved = new Set();
        const choicesGridPending = new Set();
        const choicesGridImages = new WeakSet();
        let choicesGridResizeObserver = null;
        let choicesGridFrame = null;

        function getPreferredChoicesColumns(grid) {
            const stored = parseInt(grid.dataset.preferredColumns || '', 10);
            return [1, 2, 4].includes(stored) ? stored : 4;
        }

        function choicesGridOverflows(grid) {
            return Array.from(grid.children).some(item => {
                const content = item.querySelector('.choices-content') || item;
                const imageMinWidth = grid.closest('.paper-choice-options-row') ? 100 : 140;
                const imageTooSmall = Array.from(content.querySelectorAll('img')).some(img =>
                    Math.min(img.naturalWidth, imageMinWidth) > content.clientWidth + 1
                );
                return imageTooSmall || content.scrollWidth > content.clientWidth + 1;
            });
        }

        function pruneDetachedChoicesGridTargets() {
            for (const grid of choicesGridObserved) {
                if (grid.isConnected) continue;
                if (choicesGridResizeObserver) choicesGridResizeObserver.unobserve(grid);
                choicesGridObserved.delete(grid);
                choicesGridPending.delete(grid);
                choicesGridSizes.delete(grid);
            }
        }

        function adaptChoicesGridsBatch(grids) {
            // Keep this entry synchronous: pagination and scroll anchoring read
            // the final option heights immediately after preview rendering.
            const live = Array.from(new Set(grids)).filter(grid =>
                grid && grid.isConnected && grid.clientWidth > 0
            );
            if (!live.length) return;
            const preferred = live.map(getPreferredChoicesColumns);
            const selected = new Array(live.length).fill(0);
            for (const grid of live) {
                choicesGridPending.delete(grid);
                grid.querySelectorAll('.choices-math-scroll').forEach(math => math.classList.remove('choices-math-scroll'));
                grid.dataset.choicesMeasuring = 'true';
            }
            // Write every pending grid before reading any candidate. The
            // browser resolves at most one layout per candidate for this batch.
            for (const columns of [4, 2, 1]) {
                const pending = [];
                for (let index = 0; index < live.length; index++) {
                    if (selected[index] || columns > preferred[index]) continue;
                    live[index].style.setProperty('--choices-columns', String(columns));
                    pending.push(index);
                }
                if (!pending.length) continue;
                void live[pending[0]].offsetWidth;
                for (const index of pending) {
                    if (!choicesGridOverflows(live[index]) || columns === 1) selected[index] = columns;
                }
            }
            for (let index = 0; index < live.length; index++) {
                const grid = live[index];
                grid.style.setProperty('--choices-columns', String(selected[index]));
                grid.dataset.choiceColumns = String(selected[index]);
                delete grid.dataset.choicesMeasuring;
            }
            // Only indivisible inline formulas need horizontal scrolling. Use
            // layout pixels throughout, including inside the scaled A4 preview.
            const scrollingMath = [];
            for (const grid of live) {
                grid.querySelectorAll('.choices-content .katex').forEach(math => {
                    const content = math.closest('.choices-content');
                    if (!math.closest('.katex-display') && math.offsetWidth > content.clientWidth + 1) scrollingMath.push(math);
                });
            }
            for (const math of scrollingMath) math.classList.add('choices-math-scroll');
            // Capture the final border-box after all writes, so our own height
            // changes do not trigger a ResizeObserver measurement loop.
            for (const grid of live) {
                choicesGridSizes.set(grid, { width: grid.offsetWidth, height: grid.offsetHeight });
            }
        }

        function flushChoicesGridPending() {
            choicesGridFrame = null;
            const grids = Array.from(choicesGridPending);
            choicesGridPending.clear();
            pruneDetachedChoicesGridTargets();
            adaptChoicesGridsBatch(grids);
        }

        function scheduleChoicesGridLayout(grid) {
            if (!grid.isConnected) return;
            choicesGridPending.add(grid);
            if (choicesGridFrame !== null) return;
            if (typeof window.requestAnimationFrame === 'function') {
                choicesGridFrame = window.requestAnimationFrame(flushChoicesGridPending);
            } else {
                flushChoicesGridPending();
            }
        }

        function ensureChoicesGridResizeObserver() {
            if (choicesGridResizeObserver || typeof ResizeObserver === 'undefined') return;
            choicesGridResizeObserver = new ResizeObserver(entries => {
                pruneDetachedChoicesGridTargets();
                for (const entry of entries) {
                    const grid = entry.target;
                    if (!grid.isConnected) continue;
                    const previous = choicesGridSizes.get(grid);
                    const boxes = entry.borderBoxSize;
                    const box = Array.isArray(boxes) ? boxes[0] : boxes;
                    const width = box ? Math.round(box.inlineSize) : grid.offsetWidth;
                    const height = box ? Math.round(box.blockSize) : grid.offsetHeight;
                    if (previous && previous.width === width && previous.height === height) continue;
                    scheduleChoicesGridLayout(grid);
                }
            });
        }

        function adaptChoicesGridLayout(root) {
            if (!root) return;
            const grids = [];
            if (root.matches && root.matches('.choices-grid')) grids.push(root);
            if (root.querySelectorAll) grids.push(...root.querySelectorAll('.choices-grid'));
            ensureChoicesGridResizeObserver();
            pruneDetachedChoicesGridTargets();
            adaptChoicesGridsBatch(grids);
            for (const grid of grids) {
                if (!grid.isConnected) continue;
                grid.querySelectorAll('img').forEach(img => {
                    if (!img.complete && !choicesGridImages.has(img)) {
                        choicesGridImages.add(img);
                        const update = () => scheduleChoicesGridLayout(grid);
                        img.addEventListener('load', update);
                        img.addEventListener('error', update);
                    }
                });
                if (choicesGridResizeObserver && !choicesGridObserved.has(grid)) {
                    choicesGridObserved.add(grid);
                    choicesGridResizeObserver.observe(grid);
                }
            }
        }
        window.adaptChoicesGridLayout = adaptChoicesGridLayout;

        function setupRealtimePreviews() {
            const editContent = document.getElementById('editContent');
            const editAnswer = document.getElementById('editAnswerMarkdown');

            const updateContentPreview = () => {
                const text = editContent.value;
                const previewContainer = document.getElementById('contentPreview');
                const paperContainer = document.getElementById('paperContent');
                
                // Rebuild after every edit, including undo and pasted Markdown.
                if (typeof window.syncEditorImageReferences === 'function') {
                    window.syncEditorImageReferences();
                    renderIllustrationBadges();
                } else if (uploadedImages.length > 0) {
                    uploadedImages = uploadedImages.filter(path => text.includes(path));
                    renderIllustrationBadges();
                }
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
                    return;
                }
                
                const referenceContext = window.QuestionReferenceState.context('content', text);
                const preparedHtml = renderQuestionPreviewContent(previewContainer, text, {
                    imageLayouts: FigureLayoutState.imageLayouts, referenceContext
                });
                renderQuestionPreviewContent(paperContainer, text, {
                    preparedHtml, imageLayouts: FigureLayoutState.imageLayouts, referenceContext
                });
                if (typeof window.applyEditorFigureLayoutPreview === 'function') {
                    window.applyEditorFigureLayoutPreview(previewContainer, text);
                    window.applyEditorFigureLayoutPreview(paperContainer, text);
                }
            };

            const updateAnswerPreview = () => {
                // Automatically synchronize answer image badges on manual or programmatic text changes
                if (typeof syncAnswerImagesFromMarkdown === 'function') {
                    syncAnswerImagesFromMarkdown();
                }
                const text = editAnswer.value;
                const previewContainer = document.getElementById('answerPreview');
                const paperContainer = document.getElementById('paperAnalysisContent');
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示极其精美的 LaTeX 排版...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
                    return;
                }
                
                let html = parseMarkdownWithMath(text, {}, window.QuestionReferenceState.context('answer_markdown', text));
                previewContainer.innerHTML = html;
                paperContainer.innerHTML = html;
                
                try {
                    renderMathInElement(previewContainer, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                    renderMathInElement(paperContainer, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    console.error(e);
                }
                adaptChoicesGridLayout(previewContainer);
                adaptChoicesGridLayout(paperContainer);
            };
            
            // Attach inputs
            editContent.addEventListener('input', debounce(updateContentPreview, 250));
            editAnswer.addEventListener('input', debounce(updateAnswerPreview, 250));
            
            const editReview = document.getElementById('editReview');
            const updateReviewPreview = () => {
                const text = editReview.value;
                const wrapper = document.getElementById('paperReviewWrapper');
                const content = document.getElementById('paperReviewContent');
                
                if (!text.trim()) {
                    if (wrapper) wrapper.classList.add('hidden');
                    if (content) content.innerHTML = '';
                    return;
                }
                
                if (wrapper) wrapper.classList.remove('hidden');
                let html = parseMarkdownWithMath(text, {}, window.QuestionReferenceState.context('review', text));
                if (content) content.innerHTML = html;
                
                try {
                    if (content) {
                        renderMathInElement(content, {
                            delimiters: [
                                {left: '$$', right: '$$', display: true},
                                {left: '$', right: '$', display: false},
                                {left: '\\(', right: '\\)', display: false},
                                {left: '\\[', right: '\\]', display: true}
                            ],
                            throwOnError: false
                        });
                    }
                } catch(e) {
                    console.error('KaTeX review rendering error: ', e);
                }
                if (content) adaptChoicesGridLayout(content);
            };
            editReview.addEventListener('input', debounce(updateReviewPreview, 250));
            
            // Expose update preview functions to global scope to allow synchronous direct updates when loading questions/drafts
            window.updateContentPreview = updateContentPreview;
            window.updateAnswerPreview = updateAnswerPreview;
            window.updateReviewPreview = updateReviewPreview;
            
            // Keep all editor metadata preview updates on one rendering path.
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            editQType.addEventListener('change', renderEditorPaperMeta);
            editDifficulty.addEventListener('change', renderEditorPaperMeta);
            editSource.addEventListener('input', renderEditorPaperMeta);
            if (editTags) editTags.addEventListener('input', renderEditorPaperMeta);
            
            // Initial render of meta badges
            renderEditorPaperMeta();
        }

        function cleanChoiceStemParentheses(text) {
            if (!text) return "";
            text = text.trim();
            text = text.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();
            const pattern = /(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*\$?[\s\xa0\u3000]*)+$/;
            let cleaned = text.replace(pattern, '').replace(/\\paren\b/g, '').trim();
            cleaned = cleaned.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();

            cleaned = normalizePreviewDollarSigns(cleaned);
            const outsideMath = replaceDelimitedMathForPreview(cleaned, () => '');
            const sanitizedDollars = outsideMath.replace(previewLiteralPattern(), '').replace(/\\\$/g, '');
            const dollarCount = (sanitizedDollars.match(/\$/g) || []).length;
            if (dollarCount % 2 !== 0) {
                cleaned += '$';
            }

            return cleaned;
        }
        window.cleanChoiceStemParentheses = cleanChoiceStemParentheses;

        function transformFillinMacro(clean) {
            if (!clean) return "";
            const saved = [];
            const prefix = previewGuardPrefix(clean, 'F');
            const save = (original, kind = 'literal') => {
                const marker = prefix + saved.length + '\uE003';
                saved.push({marker, original, kind});
                return marker;
            };
            function optionMath(value) {
                if (value === undefined) return {value, safe: true};
                let option = value;
                if (option.includes(prefix)) {
                    const item = saved.find(entry => entry.marker === option.trim());
                    if (!item || item.kind !== 'math') return {value, safe: false};
                    option = item.original;
                }
                let complete = false;
                const normalized = replaceDelimitedMathForPreview(option, (whole, inner) => {
                    if (whole !== option.trim()) return whole;
                    complete = true;
                    return inner;
                });
                if (!complete) {
                    for (let index = 0; index < option.length; index++) {
                        if ((option[index] === '$' || option.startsWith('\\(', index) || option.startsWith('\\[', index))
                                && !isLatexTokenEscaped(option, index)) return {value, safe: false};
                    }
                }
                return {value: normalized, safe: true};
            }
            function replaceMacros(source, insideMath) {
                return source.replace(/\\fillin(?:\[([^\]]*?)\])?(?:\[([^\]]*?)\])?/g, function(match, p1, p2, offset) {
                    if (isLatexTokenEscaped(source, offset)) return match;
                    const first = optionMath(p1), second = optionMath(p2);
                    if (!first.safe || !second.safe) return match;
                    function isLengthStr(str) {
                        return /^\s*\d+(?:\.\d+)?\s*(?:cm|mm|in|pt|pc|em|ex)\s*$/i.test(str || '');
                    }

                    let innerTex = '\\underline{\\hspace{1.5cm}}';
                    if (p1 !== undefined && p2 !== undefined) {
                        const len = isLengthStr(p1) ? p1 : '1.5cm';
                        innerTex = '\\underline{\\hspace{' + len + '}' + second.value + '\\hspace{' + len + '}}';
                    } else if (p1 !== undefined) {
                        if (isLengthStr(p1)) {
                            innerTex = '\\underline{\\hspace{' + p1 + '}}';
                        } else {
                            innerTex = '\\underline{\\quad ' + first.value + ' \\quad}';
                        }
                    }

                    // Complete math keeps its original delimiters. A prose
                    // blank owns separate delimiters, even next to $x$.
                    return insideMath ? innerTex : '\\(' + innerTex + '\\)';
                });
            }
            let source = replacePreviewLiteralSpans(clean, original => save(original));
            source = replaceDelimitedMathForPreview(source, (whole, inner, opening, closing) =>
                save(opening + replaceMacros(inner, true) + closing, 'math'));
            source = replaceMacros(source, false);
            saved.slice().reverse().forEach(({marker, original}) => {
                source = source.split(marker).join(original);
            });
            return source;
        }

        function createMathMLSymbol(symbol, attributes = {}) {
            // A fresh MathML leaf for each occurrence: parent builders may
            // add attributes. Keep semantic text alongside the local SVG.
            return {
                type: 'mo', children: [], classes: [],
                attributes: { lspace: '0em', rspace: '0em', stretchy: 'false', ...attributes },
                setAttribute(key, value) { this.attributes[key] = String(value); },
                getAttribute(key) { return this.attributes[key]; },
                toText() { return symbol; },
                toNode() {
                    const node = document.createElementNS('http://www.w3.org/1998/Math/MathML', 'mo');
                    Object.keys(this.attributes).forEach(key => node.setAttribute(key, this.attributes[key]));
                    node.textContent = symbol;
                    return node;
                },
                toMarkup() {
                    const escape = value => String(value).replace(/[&<>"']/g, char => ({
                        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                    }[char]));
                    const attributes = Object.keys(this.attributes)
                        .map(key => ' ' + key + '="' + escape(this.attributes[key]) + '"').join('');
                    return '<mo' + attributes + '>' + escape(symbol) + '</mo>';
                }
            };
        }

        function registerParallelogramSymbol(renderer) {
            // The bundled KaTeX extension API keeps this symbol in every
            // rendering path, including text, scripts and fractions. Draw a
            // fixed local outline instead of depending on a system font, and
            // leave KaTeX's untrusted-command policy unchanged.
            const { Span, SvgNode, PathNode } = renderer.__domTree;
            renderer.__defineFunction({
                type: 'mathbankParallelogram',
                names: ['\\parallelogram'],
                props: { numArgs: 0, allowedInText: true, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankParallelogram', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const path = new PathNode('mathbankParallelogram',
                        'M230 0H930L700 600H0Z M266 50L70 550H665L860 50Z');
                    const svg = new SvgNode([path], {
                        width: '0.93em', height: '0.6em', viewBox: '0 0 930 600',
                        preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true'
                    });
                    const span = new Span(['mord', 'mb-parallelogram'], [svg], options, {
                        display: 'inline-block', position: 'relative', width: '0.93em', height: '0.6em'
                    });
                    span.height = 0.6;
                    span.depth = 0;
                    span.width = 0.93;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('▱')
            });
            renderer.__defineMacro('▱', '\\parallelogram');
        }
        function registerSchoolMathSymbols(renderer) {
            const { Span, SvgNode, PathNode } = renderer.__domTree;
            // exam-zh-symbols defines these zero-argument upright constants.
            // Register macros in the shared renderer so every preview keeps
            // the original TeX and ordinary variable i/e remain italic.
            ['iu', 'upi'].forEach(name => renderer.__defineMacro('\\' + name, '\\mathrm{i}'));
            ['eu', 'upe'].forEach(name => renderer.__defineMacro('\\' + name, '\\mathrm{e}'));
            renderer.__defineMacro('\\ensuremath', context =>
                context.mode === 'text' ? '\\({#1}\\)' : '{#1}');
            renderer.__defineFunction({
                type: 'mathbankArcAccent', names: ['\\mathbankArcAccent'],
                props: { numArgs: 0, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankArcAccent', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const path = new PathNode('mathbankArcAccent',
                        'M0 280 Q500 -200 1000 280 L1000 330 Q500 -150 0 330 Z');
                    const svg = new SvgNode([path], {
                        width: '100%', height: '0.33em', viewBox: '0 0 1000 330',
                        preserveAspectRatio: 'none', 'aria-hidden': 'true'
                    });
                    // The relative vlist row produced by overset has the base's
                    // actual width. Keep top/bottom auto for its static baseline;
                    // KaTeX still lays out the base, nested macros and scripts.
                    const span = new Span(['mord', 'mb-arc-accent'], [svg], options, {
                        position: 'absolute', left: '0', width: '100%', height: '0.33em'
                    });
                    span.height = 0.33;
                    span.depth = span.width = 0;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('⏜', { stretchy: 'true', accent: 'true' })
            });
            ['wideparen', 'overparen', 'widearc'].forEach(name => {
                renderer.__defineMacro('\\' + name, '\\overset{\\mathbankArcAccent}{#1}');
            });
            renderer.__defineMacro('\\overarc', context => {
                context.consumeSpaces();
                if (context.future().text === '[') {
                    context.popToken();
                    let value = '';
                    while (!['EOF', ']'].includes(context.future().text)) value += context.popToken().text;
                    if (context.future().text !== ']' || value.trim() !== '1') {
                        throw new renderer.ParseError('\\overarc supports the default width only; use \\wideparen{...}');
                    }
                    context.popToken();
                }
                return '\\wideparen';
            });
            renderer.__defineFunction({
                type: 'mathbankPerThousand', names: ['\\perthousand', '\\textperthousand', '\\permil'],
                props: { numArgs: 0, allowedInText: true, allowedInArgument: true },
                handler: ({ parser }) => ({ type: 'mathbankPerThousand', mode: parser.mode }),
                htmlBuilder: (group, options) => {
                    const ring = (x, y) => `M${x} ${y - 110}a110 110 0 1 1 0 220a110 110 0 1 1 0 -220Z`
                        + `M${x} ${y - 70}a70 70 0 1 0 0 140a70 70 0 1 0 0 -140Z`;
                    const path = new PathNode('mathbankPerThousand',
                        ring(140, 140) + ring(530, 555) + ring(810, 555) + 'M570 30H625L245 665H190Z');
                    const svg = new SvgNode([path], {
                        width: '0.95em', height: '0.7em', viewBox: '0 0 950 700',
                        preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true'
                    });
                    const span = new Span(['mord', 'mb-perthousand'], [svg], options, {
                        display: 'inline-block', position: 'relative', width: '0.95em', height: '0.7em'
                    });
                    span.height = 0.7;
                    span.depth = 0;
                    span.width = 0.95;
                    span.maxFontSize = options.sizeMultiplier;
                    return span;
                },
                mathmlBuilder: () => createMathMLSymbol('‰')
            });
            renderer.__defineMacro('‰', '\\perthousand');
            renderer.__defineMacro('\\celsius', '\\ensuremath{{}^\\circ\\mathrm{C}}');
            renderer.__defineMacro('℃', '\\celsius');
            renderer.__defineMacro('\\sfrac', '\\ensuremath{{}^{#1}\\!/\\!{}_{#2}}');
            renderer.__defineMacro('\\ang', context => {
                context.consumeSpaces();
                if (context.future().text === '[') {
                    throw new renderer.ParseError('\\ang optional formatting is not supported; use its default numeric form');
                }
                const input = context.consumeArgs(1)[0].slice().reverse().map(token => token.text).join('');
                const parts = input.split(';').map(part => part.trim());
                if (parts.length > 3 || !parts.some(Boolean)
                    || parts.some(part => part && !/^[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/.test(part))) {
                    throw new renderer.ParseError('\\ang expects a number or numeric degrees;minutes;seconds');
                }
                const units = ['\\circ', '\\prime', '\\prime\\prime'];
                return '\\ensuremath{' + parts.map((part, index) => part
                    ? part.replace(',', '.') + '^{' + units[index] + '}' : '').join('') + '}';
            });
        }
        if (typeof katex !== 'undefined') {
            katex.__defineMacro('\\textdollar', '\\text{\\$}');
            katex.__defineMacro('\\strut', '\\rule[-0.36em]{0em}{1.2em}');
            registerParallelogramSymbol(katex);
            registerSchoolMathSymbols(katex);
        }

        function transformExamZhParenForPreview(text) {
            if (!text) return "";
            return text.replace(/\\paren\b/g, '<span class="exam-zh-paren-preview" role="img" aria-label="选择题作答括号">（&nbsp;&nbsp;）</span>');
        }

        function normalizeNakedMathForPreview(text) {
            if (!text) return "";
            let source = String(text);

            const placeholders = [];
            function save(original) {
                const marker = `\uE000${placeholders.length}\uE001`;
                placeholders.push({ marker, original });
                return marker;
            }
            function protect(pattern, transform) {
                source = source.replace(pattern, function(match) {
                    return save(typeof transform === 'function' ? transform(match) : match);
                });
            }

            source = replacePreviewLiteralSpans(source, save);

            [
                /```[\s\S]*?```/g,
                /`[^`\n]*`/g,
                /\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}/g,
                /!\[[^\]\n]*\]\([^\n)]*\)/g,
                /\]\([^\n)]*\)/g,
                /<mathbank-math\b[^>]*>[\s\S]*?<\/mathbank-math\s*>/g,
                /\[\[MBM_[A-Za-z0-9_:-]+\]\]/g,
                /\[ILLUSTRATION_BOX:\s*[^\]\n]*\]/gi,
                // Cells already contain these tokens when table rendering
                // reaches this helper. They must survive until final restore.
                /@@MATH_PLACEHOLDER_\d+@@/g
            ].forEach(function(pattern) { protect(pattern); });

            source = normalizePreviewDollarSigns(source);
            source = replaceDelimitedMathForPreview(source, save);
            source = replaceLatexEnvironmentsForPreview(source, function(environment, name, body) {
                if (/^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)$/.test(name)) {
                    return save(environment);
                }
                if (/^(equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath)$/.test(name)) {
                    // KaTeX has no multline environment. Preserve every row
                    // in a centered gathered preview; stored/exported TeX is unchanged.
                    const innerEnvironment = /^alignat/.test(name) ? 'alignedat'
                        : /^align/.test(name) ? 'aligned'
                        : /^(gather|multline)/.test(name) ? 'gathered' : '';
                    const preview = innerEnvironment
                        ? '\\begin{' + innerEnvironment + '}' + body + '\\end{' + innerEnvironment + '}'
                        : body;
                    return save('$$' + preview + '$$');
                }
                return save('$' + (name === 'math' ? body : environment) + '$');
            });
            // Save dollar math and native math environments before URLs, so a
            // URL inside either cannot conceal its closing delimiter/environment.
            protect(/[hH][tT][tT][pP][sS]?:\/\/[^\s<>"']+/g);

            // Bold prose is unpacked after normalization. Normalize geometry
            // within that text now, while keeping the outer textbf protected.
            protect(/\\textbf\{[^{}\n]*\}/g, function(match) {
                if (!/[▱‰℃]|\\(?:parallelogram|perthousand|textperthousand|permil|celsius)(?![A-Za-z])/.test(match)) return match;
                return '\\textbf{' + normalizeNakedMathForPreview(match.slice(8, -1)) + '}';
            });
            // Semicolons in siunitx degree/minute/second input belong to one
            // formula, rather than separating prose-level math candidates.
            protect(/\\ang\s*\{[+\-\d.,;\s]*\}/g, match => '\\(' + match + '\\)');

            [
                // Choice layout keys are TeX parameters, not naked equations.
                /\\begin\{choices\}(?:\[[^\]\n]*\])?/g,
                /\\(?:begin|end)\{[^}\n]+\}/g,
                /\\item\b/g,
                /\\fillin\b/g,
                /\\paren\b/g,
                /\\includegraphics(?:\s*\[[^\]\n]*\])?\s*\{[^}\n]+\}/g,
                /<\/?[A-Za-z][^>\n]*>/g
            ].forEach(function(pattern) { protect(pattern); });

            source = source.replace(/[A-Za-z0-9▱‰℃\\{}_^+\-*/=<>|(),.:\[\]\t ]+/g, function(raw) {
                const core = raw.trim();
                if (!core) return raw;
                const nonMathCommands = new Set([
                    'begin', 'bottomrule', 'centering', 'cline', 'end', 'fillin',
                    'hline', 'includegraphics', 'item', 'midrule', 'multicolumn',
                    'multirow', 'paren', 'renewcommand', 'textbf', 'toprule'
                ]);
                const commands = core.match(/\\[A-Za-z]+/g) || [];
                const hasMathCommand = commands.some(command => !nonMathCommands.has(command.slice(1).toLowerCase()));
                const hasScript = /[A-Za-z0-9})\]]\s*[_^](?:\s*\{|\s*[A-Za-z0-9\\])/.test(core);
                const hasRelation = /[A-Za-z0-9})\]]\s*(?:=|<|>)\s*(?:[A-Za-z0-9({\[\\+\-])/.test(core);
                const hasFunction = /\b[A-Za-z]\s*\([^)]*[A-Za-z0-9_+\-,\\][^)]*\)/.test(core);
                const hasCoordinate = /\(\s*[+\-]?(?:\d+(?:\.\d+)?|[A-Za-z])\s*,[^)]*\)/.test(core);
                if (!hasMathCommand && !hasScript && !hasRelation && !hasFunction && !hasCoordinate) {
                    // A pasted symbol alone must not turn surrounding English
                    // prose into math or consume adjacent dollar delimiters.
                    return raw.replace(/[▱‰℃]/g, symbol => '\\(' + symbol + '\\)');
                }
                const leading = raw.slice(0, raw.length - raw.trimStart().length);
                const trailing = raw.slice(raw.trimEnd().length);
                return leading + '$' + core + '$' + trailing;
            });

            placeholders.slice().reverse().forEach(function(entry) {
                source = source.replace(entry.marker, function() { return entry.original; });
            });
            return source;
        }

        function previewLiteralPattern() {
            return /```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|(`+)[\s\S]*?\1(?!`)|\\begin\{(tikzpicture|verbatim\*?|Verbatim\*?|lstlisting|minted|comment)\}[\s\S]*?(?:\\end\{\2\}|$)|\\(?:verb|Verb|lstinline)\*?(?:\[[^\]\n]*\])?([^\w\s{])[^\n]*?(?:\3|(?=\r?\n)|$)|\\mintinline(?:\[[^\]\n]*\])?\{[^{}\n]*\}([^\w\s{])[^\n]*?(?:\4|(?=\r?\n)|$)|\\(?:detokenize|url|path|lstinline\*?(?:\[[^\]\n]*\])?|mintinline(?:\[[^\]\n]*\])?\{[^{}\n]*\})\s*\{|<(?:[cC][oO][dD][eE]|[pP][rR][eE])\b[^>]*>[\s\S]*?(?:<\/(?:[cC][oO][dD][eE]|[pP][rR][eE])\s*>|$)|!\[[^\]\n]*\]\([^\n)]*\)|\]\([^\n)]*\)|[hH][tT][tT][pP][sS]?:\/\/[^\s<>"']+|<mathbank-math\b[^>]*>[\s\S]*?<\/mathbank-math\s*>|<\/?[A-Za-z][^>\n]*>|\[\[MBM_[A-Za-z0-9_:-]+\]\]/g;
        }

        function previewLiteralEnd(text, match) {
            let end = match.index + match[0].length;
            const tail = text.slice(match.index);
            const opening = /^(`{3,}|~{3,})([^\r\n]*)/.exec(tail);
            const lineStart = text.lastIndexOf('\n', match.index - 1) + 1;
            if (opening && /^[ \t]{0,3}$/.test(text.slice(lineStart, match.index))
                    && !(opening[1][0] === '`' && opening[2].includes('`'))) {
                const newline = tail.indexOf('\n');
                if (newline < 0) return text.length;
                const bodyStart = match.index + newline + 1;
                const closing = new RegExp('^[ \\t]{0,3}' + opening[1][0]
                    + '{' + opening[1].length + ',}[ \\t]*\\r?$', 'm').exec(text.slice(bodyStart));
                return closing ? bodyStart + closing.index + closing[0].length : text.length;
            }
            // Inline code may span lines and use three or more ticks; the
            // broad fence token must not stop at a shorter run in its payload.
            const ticks = /^`+/.exec(tail);
            if (ticks) {
                const closing = new RegExp('`+', 'g');
                closing.lastIndex = match.index + ticks[0].length;
                let next;
                while ((next = closing.exec(text))) {
                    if (next[0].length === ticks[0].length) return closing.lastIndex;
                }
            }
            if (!match[0].endsWith('{') || !/^\\(?:detokenize|url|path|lstinline|mintinline)\b/.test(match[0])) return end;
            let depth = 1;
            while (end < text.length && depth > 0) {
                if (!isLatexTokenEscaped(text, end)) depth += (text[end] === '{') - (text[end] === '}');
                end++;
            }
            return end;
        }

        function previewGuardPrefix(text, kind) {
            let prefix = '\uE002' + kind;
            while (text.includes(prefix)) prefix += kind;
            return prefix;
        }

        function replacePreviewLiteralSpans(text, replace) {
            // The existing scanner gives an earlier math opener precedence.
            // Mask complete math first, then visit only actual literal spans.
            const guards = [];
            const prefix = previewGuardPrefix(text, 'G');
            const saveMath = whole => {
                const marker = prefix + guards.length + '\uE003';
                guards.push({marker, whole});
                return marker;
            };
            let source = replaceDelimitedMathForPreview(text, saveMath);
            source = replaceLatexEnvironmentsForPreview(source, (environment, name) =>
                /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)$/.test(name) ? environment : saveMath(environment));
            const pattern = previewLiteralPattern();
            const parts = [];
            let copied = 0, match;
            while ((match = pattern.exec(source))) {
                const end = previewLiteralEnd(source, match);
                parts.push(source.slice(copied, match.index), replace(source.slice(match.index, end)));
                copied = pattern.lastIndex = end;
            }
            parts.push(source.slice(copied));
            source = parts.join('');
            guards.slice().reverse().forEach(({marker, whole}) => {
                source = source.split(marker).join(whole);
            });
            return source;
        }

        function normalizePreviewDollarSigns(text) {
            const literal = '\\(\\text{\\$}\\)';
            const tokens = new RegExp(previewLiteralPattern().source + '|'
                + /\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\\textdollar\b(?:\{\})?|\$/.source, 'g');
            let result = '', copied = 0, match;
            while ((match = tokens.exec(text))) {
                const token = match[0], start = match.index;
                if (isLatexTokenEscaped(text, start)) continue;
                const literalEnd = previewLiteralEnd(text, match);
                if (literalEnd > tokens.lastIndex) tokens.lastIndex = literalEnd;
                if (token.startsWith('\\textdollar')) {
                    result += text.slice(copied, start) + literal;
                    copied = tokens.lastIndex;
                } else if (token === '$') {
                    let end = text.indexOf('$', start + 1);
                    while (end >= 0 && isLatexTokenEscaped(text, end)) end = text.indexOf('$', end + 1);
                    const tail = text.slice(start + 1);
                    // A complete numerical product such as $2 xy+3$ is math,
                    // even though its first variable looks like a prose word.
                    // Money-list connectors still identify $5 and $10 as prose.
                    const paired = end >= 0
                        ? /^\d[\d,]*(?:\.\d+)?[ \t]+([A-Za-z][A-Za-z0-9]*)([\s\S]*)$/.exec(text.slice(start + 1, end))
                        : null;
                    const remainder = paired ? paired[2].trim() : '';
                    const mathContinuation = remainder
                        && /^[+\-*/^_=<>\\([{]|^[A-Za-z0-9](?:\b|[_^])/.test(remainder)
                        && !/[.!?;:][ \t\r\n]+[A-Za-z]{2,}\b/.test(remainder);
                    const productMath = paired
                        && (mathContinuation || (!remainder
                            && !/^(?:and|or|to|for|at|each|per|costs?|price|total|dollars?|tax)$/i.test(paired[1])));
                    // Prose amounts are currency; $5$, $2+3$ and multiline
                    // formulas retain their original mathematical meaning.
                    const currency = /^\d[\d,]*(?:\.\d+)?(?:[.,!?;:]?\s+[A-Za-z]{2,}\b|[.,!?;:][ \t]*\r?\n|[.,!?;:]?(?:\s*$))/.test(tail);
                    const isolated = (start === 0 || text[start - 1] === '\n')
                        && /^[ \t]*(?:\n[ \t]*\n|\n[ \t]*\\begin\{choices\}|$)/.test(tail);
                    const amountList = /^\d[\d,]*(?:\.\d+)?[.,;:]?[ \t]+\$\d/.test(tail);
                    if ((!productMath && (currency || amountList)) || isolated || end < 0) {
                        result += text.slice(copied, start) + literal;
                        copied = start + 1;
                    } else {
                        tokens.lastIndex = end + 1;
                    }
                }
            }
            return result + text.slice(copied);
        }

        function isLatexTokenEscaped(text, index) {
            let cursor = index - 1;
            while (cursor >= 0 && text[cursor] === '\\') cursor--;
            return (index - cursor - 1) % 2 === 1;
        }

        function replaceDelimitedMathForPreview(text, replace) {
            const parts = [];
            // Paths and literal examples cannot open a math span that ends in
            // a later real formula. A math opener encountered first still owns
            // its complete contents, including URL-looking text inside math.
            const literalPattern = previewLiteralPattern();
            let literal = literalPattern.exec(text);
            let cursor = 0;
            let copied = 0;
            while (cursor < text.length) {
                if (literal && literal.index < cursor) {
                    literalPattern.lastIndex = cursor;
                    literal = literalPattern.exec(text);
                }
                if (literal && literal.index === cursor) {
                    cursor = previewLiteralEnd(text, literal);
                    literalPattern.lastIndex = cursor;
                    literal = literalPattern.exec(text);
                    continue;
                }
                const opening = ['$$', '$', '\\(', '\\['].find(token => text.startsWith(token, cursor));
                if (!opening || isLatexTokenEscaped(text, cursor)) {
                    cursor++;
                    continue;
                }
                const closing = opening === '\\(' ? '\\)' : opening === '\\[' ? '\\]' : opening;
                let end = text.indexOf(closing, cursor + opening.length);
                while (end >= 0 && isLatexTokenEscaped(text, end)) {
                    end = text.indexOf(closing, end + closing.length);
                }
                if (end < 0) {
                    cursor += opening.length;
                    continue;
                }
                const finish = end + closing.length;
                parts.push(text.slice(copied, cursor), replace(
                    text.slice(cursor, finish), text.slice(cursor + opening.length, end), opening, closing
                ));
                cursor = copied = finish;
            }
            parts.push(text.slice(copied));
            return parts.join('');
        }

        function replaceLatexEnvironmentsForPreview(text, replace, allEnvironments = false) {
            const recognized = /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath|math|cases|aligned|alignedat|gathered|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix|array|split)$/;
            const tokenPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
            const literalPattern = previewLiteralPattern();
            let literal = literalPattern.exec(text);
            const parts = [];
            let copied = 0;
            let match;
            while ((match = tokenPattern.exec(text))) {
                while (literal && literal.index <= match.index) {
                    const end = previewLiteralEnd(text, literal);
                    if (end > match.index) {
                        tokenPattern.lastIndex = end;
                        break;
                    }
                    literalPattern.lastIndex = end;
                    literal = literalPattern.exec(text);
                }
                if (literal && literal.index <= match.index && previewLiteralEnd(text, literal) > match.index) continue;
                const name = match[2];
                if (match[1] !== 'begin' || (!allEnvironments && !recognized.test(name))
                        || isLatexTokenEscaped(text, match.index)) continue;
                const bodyStart = tokenPattern.lastIndex;
                const nestedPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
                nestedPattern.lastIndex = bodyStart;
                const stack = [name];
                let nested;
                let complete = false;
                while ((nested = nestedPattern.exec(text))) {
                    if (isLatexTokenEscaped(text, nested.index)) continue;
                    if (nested[1] === 'begin') stack.push(nested[2]);
                    else if (nested[2] !== stack[stack.length - 1]) break;
                    else stack.pop();
                    if (stack.length === 0) {
                        const end = nestedPattern.lastIndex;
                        parts.push(text.slice(copied, match.index), replace(
                            text.slice(match.index, end), name, text.slice(bodyStart, nested.index)
                        ));
                        copied = tokenPattern.lastIndex = end;
                        complete = true;
                        break;
                    }
                }
                if (allEnvironments && !complete) {
                    // A malformed/unclosed TeX block cannot provide reliable
                    // Markdown row boundaries. Leave its entire tail intact.
                    parts.push(text.slice(copied, match.index), replace(text.slice(match.index), name, ''));
                    copied = text.length;
                    break;
                }
            }
            parts.push(text.slice(copied));
            return parts.join('');
        }
        window.normalizeNakedMathForPreview = normalizeNakedMathForPreview;

        function padTallMathRowsForPreview(text) {
            // A transparent native KaTeX box contributes real height/depth to
            // array layout. CSS line-height and arraystretch cannot add space
            // once a tall fraction already exceeds the default row strut.
            function groupEnd(source, start, opening = '{', closing = '}') {
                if (source[start] !== opening) return -1;
                let depth = 0;
                for (let index = start; index < source.length; index++) {
                    if (isLatexTokenEscaped(source, index)) continue;
                    if (source[index] === opening) depth++;
                    else if (source[index] === closing && --depth === 0) return index + 1;
                }
                return -1;
            }

            function padEnvironment(environment, name, originalBody, commentMarkers) {
                if (!originalBody) return environment;
                const bodyStart = environment.indexOf('}') + 1;
                const rebuild = body => environment.slice(0, bodyStart) + body
                    + environment.slice(bodyStart + originalBody.length);
                const displayStyle = /^(aligned|alignedat|gathered|split|align\*?|alignat\*?|gather\*?)$/.test(name);
                const textStyle = /^(array|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|cases)$/.test(name);
                if (!displayStyle && !textStyle) {
                    return rebuild(padEnvironments(originalBody, commentMarkers));
                }
                // Do not reinterpret literal separators or malformed groups.
                if (/\\(?:verb|Verb|lstinline|mintinline)\b/.test(originalBody)) return environment;
                let prefixEnd = 0;
                const spaces = () => { while (/\s/.test(originalBody[prefixEnd] || '')) prefixEnd++; };
                spaces();
                if (/^(array|aligned|alignedat|gathered)$/.test(name) && originalBody[prefixEnd] === '[') {
                    prefixEnd = groupEnd(originalBody, prefixEnd, '[', ']');
                    if (prefixEnd < 0) return environment;
                    spaces();
                }
                if (/^(array|alignedat|alignat\*?)$/.test(name)) {
                    prefixEnd = groupEnd(originalBody, prefixEnd);
                    if (prefixEnd < 0) return environment;
                }

                const body = originalBody.slice(prefixEnd);
                const pieces = [];
                const environments = [];
                let depth = 0, copied = 0;
                for (let index = 0; index < body.length; index++) {
                    if (isLatexTokenEscaped(body, index)) continue;
                    if (body[index] === '%') {
                        const end = body.indexOf('\n', index);
                        index = end < 0 ? body.length : end;
                        continue;
                    }
                    if (body[index] === '\\') {
                        const nested = /^\\(begin|end)\{([^{}\n]+)\}/.exec(body.slice(index));
                        if (nested) {
                            if (nested[1] === 'begin') environments.push(nested[2]);
                            else if (environments.pop() !== nested[2]) return environment;
                            index += nested[0].length - 1;
                            continue;
                        }
                    }
                    if (body[index] === '{') depth++;
                    else if (body[index] === '}' && --depth < 0) return environment;
                    if (depth || environments.length) continue;
                    let end = index;
                    if (body[index] === '&') end++;
                    else if (body.startsWith('\\\\', index) || /^\\cr\b/.test(body.slice(index))) {
                        end += body.startsWith('\\\\', index) ? 2 : 3;
                        if (body[end] === '*') end++;
                        const gapStart = end + (/^\s*/.exec(body.slice(end))[0].length);
                        if (body[gapStart] === '[') {
                            end = groupEnd(body, gapStart, '[', ']');
                            if (end < 0) return environment;
                        }
                    }
                    if (end > index) {
                        pieces.push({cell: body.slice(copied, index), separator: body.slice(index, end)});
                        copied = end;
                        index = end - 1;
                    }
                }
                if (depth || environments.length) return environment;
                pieces.push({cell: body.slice(copied), separator: ''});
                let rowCount = 0, rowHasContent = false;
                pieces.forEach(({cell, separator}) => {
                    let visible = cell.replace(/\\(?:hline|hdashline)\b/g, '');
                    commentMarkers.forEach(marker => { visible = visible.split(marker).join(''); });
                    if (visible.trim()) rowHasContent = true;
                    if (separator !== '&') {
                        if (rowHasContent) rowCount++;
                        rowHasContent = false;
                    }
                });
                const multiline = rowCount > 1;
                const style = displayStyle ? '\\displaystyle ' : '\\textstyle ';
                const padded = pieces.map(({cell, separator}) => {
                    // Keep rules and numbering at their original array level.
                    // Numbering in a cell must never be duplicated or hidden
                    // inside a text box; other cells can still gain padding.
                    const leading = /^(\s*(?:(?:\\hline|\\hdashline)\b\s*)*)/.exec(cell)[0];
                    let content = padEnvironments(cell.slice(leading.length), commentMarkers);
                    const tall = multiline && /\\(?:dfrac|cfrac|frac|dbinom|binom)(?![A-Za-z])/.test(content);
                    // A top-level tag is invisible in the cell, but must be
                    // parsed once at the array level, outside the color box.
                    let tags = '', taggedBody = '', tagCopied = 0, tagDepth = 0;
                    const tagEnvironments = [];
                    let validTags = true;
                    for (let index = 0; index < content.length; index++) {
                        if (isLatexTokenEscaped(content, index)) continue;
                        if (content[index] === '{') tagDepth++;
                        else if (content[index] === '}') tagDepth--;
                        if (content[index] !== '\\') continue;
                        const nested = /^\\(begin|end)\{([^{}\n]+)\}/.exec(content.slice(index));
                        if (nested) {
                            if (nested[1] === 'begin') tagEnvironments.push(nested[2]);
                            else tagEnvironments.pop();
                            index += nested[0].length - 1;
                            continue;
                        }
                        if (tagDepth || tagEnvironments.length) continue;
                        const tag = /^\\(?:tag(?![A-Za-z])|notag\b|nonumber\b)/.exec(content.slice(index));
                        if (!tag) continue;
                        let end = index + tag[0].length;
                        if (tag[0] === '\\tag') {
                            if (content[end] === '*') end++;
                            end += /^\s*/.exec(content.slice(end))[0].length;
                            end = groupEnd(content, end);
                            if (end < 0) { validTags = false; break; }
                        }
                        taggedBody += content.slice(tagCopied, index);
                        tags += content.slice(index, end);
                        tagCopied = end;
                        index = end - 1;
                    }
                    taggedBody += content.slice(tagCopied);
                    if (tall && validTags && !/\\(?:tag|notag|nonumber|hline|hdashline)\b/.test(taggedBody)) {
                        // Explicit style restores the cell's original math
                        // style after colorbox enters text mode. A newline
                        // prevents a trailing TeX comment consuming our braces.
                        content = '\\colorbox{transparent}{\\ensuremath{' + style + taggedBody + '\n}}' + tags;
                    }
                    return leading + content + separator;
                }).join('');
                return rebuild(originalBody.slice(0, prefixEnd) + padded);
            }
            function padEnvironments(source, inheritedComments = []) {
                // The shared environment scanner sees source tokens, so hide
                // comment payloads before it can mistake a commented end for
                // the real end. Escaped percentages still belong to the math.
                const comments = [];
                const prefix = previewGuardPrefix(source, 'C');
                let protectedSource = '', copied = 0;
                for (let index = source.indexOf('%'); index >= 0; index = source.indexOf('%', index + 1)) {
                    if (isLatexTokenEscaped(source, index)) continue;
                    const newline = source.indexOf('\n', index);
                    const end = newline < 0 ? source.length : newline;
                    const marker = prefix + comments.length + '\uE003';
                    comments.push({marker, original: source.slice(index, end)});
                    protectedSource += source.slice(copied, index) + marker;
                    copied = end;
                    index = end - 1;
                }
                protectedSource += source.slice(copied);
                let padded = replaceLatexEnvironmentsForPreview(protectedSource, (environment, name, body) =>
                    padEnvironment(environment, name, body, inheritedComments.concat(comments.map(comment => comment.marker))));
                comments.forEach(({marker, original}) => { padded = padded.split(marker).join(original); });
                return padded;
            }
            return replaceDelimitedMathForPreview(text, (whole, inner, opening, closing) =>
                opening + padEnvironments(inner) + closing);
        }

        function extractMarkdownTablesForPreview(text) {
            // Only complete, rectangular pipe tables are layout evidence.
            // Hide math and literal payloads before looking for column pipes;
            // neither P(A|B), code, image paths nor existing TeX tables provide
            // Markdown separators. Keep every original cell for the shared
            // renderer, instead of parsing mixed TeX as a Markdown document.
            const saved = [];
            const guardPrefix = previewGuardPrefix(text, 'D');
            const tablePrefix = previewGuardPrefix(text, 'T');
            const save = original => {
                const marker = guardPrefix + saved.length + '\uE003';
                saved.push({marker, original});
                return marker;
            };
            const restore = value => {
                saved.slice().reverse().forEach(({marker, original}) => {
                    value = value.split(marker).join(original);
                });
                return value;
            };
            let source = text.replace(/<(table|script|style|textarea)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi, save);
            source = replacePreviewLiteralSpans(source, original => {
                // The shared scanner owns the ](URL) suffix of a Markdown
                // link. Keep its closing label bracket visible to the group
                // scanner, while the complete URL remains a literal guard.
                return original.startsWith('](') ? ']' + save(original.slice(1)) : save(original);
            });
            source = replaceDelimitedMathForPreview(source, save);
            source = replaceLatexEnvironmentsForPreview(source, save, true);

            function splitRow(line) {
                // Four-space indentation belongs to a code block. Require
                // both outer pipes and at least two columns; a vertical-bar
                // expression or an incomplete table remains ordinary text.
                if (!/^[ ]{0,3}\|/.test(line)) return null;
                const value = line.trim();
                if (!value.endsWith('|') || isLatexTokenEscaped(value, value.length - 1)) return null;
                const cells = [];
                const groups = [];
                let cell = '';
                for (let index = 1; index < value.length - 1; index++) {
                    const char = value[index];
                    const escaped = isLatexTokenEscaped(value, index);
                    // Complete math/code is already guarded. An opener still
                    // visible here is unfinished, so do not guess its columns.
                    if ((char === '$' || char === '`') && !escaped) return null;
                    if ('{[('.includes(char) && !escaped) {
                        groups.push({'{':'}', '[':']', '(':')'}[char]);
                    } else if ('}])'.includes(char) && !escaped) {
                        const expected = groups.pop();
                        if (!expected || (expected !== char
                                && !([expected, char].includes(']') && [expected, char].includes(')')))) return null;
                    }
                    if (value[index] === '\\' && value[index + 1] === '|'
                            && !escaped && !groups.length) {
                        cell += '|';
                        index++;
                    } else if (value[index] === '|' && !escaped && !groups.length) {
                        cells.push(cell.trim());
                        cell = '';
                    } else {
                        cell += value[index];
                    }
                }
                cells.push(cell.trim());
                return !groups.length && cells.length >= 2 && cells.length <= 32 ? cells : null;
            }

            const lines = source.replace(/\r\n/g, '\n').split('\n');
            const output = [];
            const tables = [];
            for (let index = 0; index < lines.length; index++) {
                const header = splitRow(lines[index]);
                const separator = index + 1 < lines.length ? splitRow(lines[index + 1]) : null;
                if (!header || !separator || header.length !== separator.length
                        || !separator.every(cell => /^:?-{3,}:?$/.test(cell))) {
                    output.push(lines[index]);
                    continue;
                }
                const rows = [];
                let next = index + 2;
                let rectangular = true;
                while (next < lines.length && /^[ ]*\|/.test(lines[next])) {
                    const row = splitRow(lines[next]);
                    if (!row || row.length !== header.length) rectangular = false;
                    if (row && row.every(cell => /^:?-{3,}:?$/.test(cell))) rectangular = false;
                    if (row) rows.push(row);
                    next++;
                }
                if (!rectangular || !rows.length) {
                    output.push(...lines.slice(index, next));
                    index = next - 1;
                    continue;
                }
                const marker = tablePrefix + tables.length + '\uE003';
                tables.push({
                    marker,
                    header: header.map(restore),
                    rows: rows.map(row => row.map(restore)),
                    align: separator.map(cell => cell.startsWith(':') && cell.endsWith(':')
                        ? 'text-center' : cell.endsWith(':') ? 'text-right'
                        : 'text-left')
                });
                output.push(marker);
                index = next - 1;
            }
            return {source: restore(output.join('\n')), tables};
        }

        function stripSourceAttributionComment(text) {
            const prefix = '<!-- MathBank source attribution:\n';
            if (!text.startsWith(prefix)) return text;
            const closing = text.indexOf('-->', prefix.length);
            if (closing < 0) return text;
            const body = text.slice(prefix.length, closing);
            if (!body.endsWith('\n') || body.includes('<!--')) return text;
            if (!text.startsWith('\n-->\n\n', closing - 1)) return text;
            return text.slice(closing + '-->\n\n'.length);
        }

        function preprocessFormulaForKaTeX(text, imageLayouts = {}) {
            if (!text) return "";

            text = stripSourceAttributionComment(text);

            const markdownTables = extractMarkdownTablesForPreview(text);
            text = markdownTables.source;
            const literalEntries = [];
            const literalPrefix = previewGuardPrefix(text, 'L');
            const protectedText = replacePreviewLiteralSpans(text, original => {
                // Images retain the existing safe conversion and asset path.
                // Other raw HTML continues through the existing sanitizer.
                if (original.startsWith('![') || (original.startsWith('<')
                    && !/^<(?:code|pre)\b/i.test(original))) return original;
                const marker = literalPrefix + literalEntries.length + '\uE003';
                const htmlCode = /^<(?:code|pre)\b/i.test(original);
                const visible = htmlCode ? original.replace(/^<(?:code|pre)\b[^>]*>/i, '')
                    .replace(/<\/(?:code|pre)\s*>$/i, '') : original;
                literalEntries.push({marker, visible});
                return marker;
            });
            
            // Only remove legacy struts from prose. Inside complete math and
            // tables they are intentional row-height information.
            const strutGuards = [];
            const strutPrefix = previewGuardPrefix(protectedText, 'S');
            const saveStrutMath = original => {
                const marker = strutPrefix + strutGuards.length + '\uE003';
                strutGuards.push({marker, original});
                return marker;
            };
            let clean = replaceDelimitedMathForPreview(protectedText, saveStrutMath);
            clean = replaceLatexEnvironmentsForPreview(clean, saveStrutMath);
            clean = clean.replace(/\\vphantom\s*\{\s*[^}]*?\}/g, '').replace(/\\strut\b/g, '');
            strutGuards.slice().reverse().forEach(({marker, original}) => {
                clean = clean.split(marker).join(original);
            });

            clean = normalizeNakedMathForPreview(clean);
            clean = padTallMathRowsForPreview(clean);

            // Move terminal blank punctuation only within a complete math
            // span. The closing $ of $x$ cannot open a prose \fillin span.
            clean = replaceDelimitedMathForPreview(clean, (whole, inner, opening, closing) => {
                const punctuation = /^([\s\S]*?\\fillin(?:\[[^\]]*?\])?(?:\[[^\]]*?\])?)\s*([。，,；;！？!?\.]+)\s*$/.exec(inner);
                if (!punctuation || isLatexTokenEscaped(inner, punctuation[1].lastIndexOf('\\fillin'))) return whole;
                return opening + punctuation[1] + closing + punctuation[2];
            });

            // Transform exam-zh \fillin macro into KaTeX compatible \underline with math environment awareness
            clean = transformFillinMacro(clean);

            // Safely escape raw < and > inside math environments to \lt and \gt without lookbehind regex for maximum browser compatibility
            clean = replaceDelimitedMathForPreview(clean, function(match, inner, opening, closing) {
                let safeInner = inner.replace(/\\</g, '@@ESCAPED_LESS@@')
                                     .replace(/</g, '\\lt ')
                                     .replace(/@@ESCAPED_LESS@@/g, '\\<')
                                     .replace(/\\>/g, '@@ESCAPED_GREAT@@')
                                     .replace(/>/g, '\\gt ')
                                     .replace(/@@ESCAPED_GREAT@@/g, '\\>');
                return opening + safeInner + closing;
            });

            // Clean up illegal nesting like \underline{\quad $\mathbf{14}$ \quad} in KaTeX
            clean = clean.replace(/(\\underline\s*\{[^}]*?)\$([^$]+?)\$([^}]*?\})/g, function(match, p1, p2, p3) {
                return '$' + p1 + p2 + p3 + '$';
            });
            
            // Protect every complete math shell before wrapping a bare blank;
            // dollar counting cannot identify \( ... \) or an adjacent $x$.
            const underlineMath = [];
            const underlinePrefix = previewGuardPrefix(clean, 'U');
            clean = replaceDelimitedMathForPreview(clean, whole => {
                const marker = underlinePrefix + underlineMath.length + '\uE003';
                underlineMath.push({marker, whole});
                return marker;
            });
            clean = clean.replace(/\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}/g, match => '\\(' + match + '\\)');
            underlineMath.slice().reverse().forEach(({marker, whole}) => {
                clean = clean.split(marker).join(whole);
            });
            
            // Protect math blocks to avoid replacing spacing commands inside math environments
            const placeholders = [];
            let placeholderCounter = 0;
            
            function savePlaceholder(match) {
                const placeholder = `@@MATH_PLACEHOLDER_${placeholderCounter++}@@`;
                placeholders.push({ placeholder, original: match });
                return placeholder;
            }
            
            let tempText = clean;
            tempText = replaceDelimitedMathForPreview(tempText, savePlaceholder);
            
            // Strip HTML tags from non-math parts
            tempText = tempText.replace(/<[^>]*>/g, '');

            // exam-zh defines \paren outside math mode, while KaTeX does not.
            // Convert only the protected-and-sanitized text portion into the
            // same empty answer parentheses used by the A4 paper preview.
            tempText = transformExamZhParenForPreview(tempText);
            
            // Process LaTeX lists & environments outside math blocks
            tempText = tempText.replace(/\\\\\s*\\begin\{/g, '\\begin{')
                               .replace(/\\begin\{([^}]+?)\}\s*\\\\/g, '\\begin{$1}')
                               .replace(/\\\\\s*\\end\{/g, '\\end{')
                               .replace(/\\end\{([^}]+?)\}\s*\\\\/g, '\\end{$1}')
                               .replace(/\\\\\s*\\item/g, '\\item')
                               .replace(/\\item\s*\\\\/g, '\\item');

            // Process choices environment (exam-zh-choices). Automatic grids
            // always test 4/2/1 rendered widths; TeX length is not a width bound.
            tempText = tempText.replace(/\\begin\{choices\}(?:\[([^\]]*)\])?([\s\S]*?)\\end\{choices\}/g, function(match, parameters, inner) {
                const items = inner.split(/\\item/).map(item => item.trim()).filter(item => item.length > 0);
                const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
                const hasImages = items.some(item => /!\[[^\]]*\]\([^)]+\)/.test(item));
                const explicitColumns = /(?:^|,)\s*columns\s*=\s*(1|2|4)\s*(?:,|$)/.exec(parameters || '');
                const preferredColumns = explicitColumns ? Number(explicitColumns[1]) : 4;
                let html = `<div class="grid grid-cols-${preferredColumns} gap-2 my-2 select-none choices-grid ${hasImages ? 'choices-has-images' : ''} items-baseline" data-preferred-columns="${preferredColumns}">`;
                items.forEach((item, idx) => {
                    const label = labels[idx] || (idx + 1);
                    let cleanItem = item;
                    // Auto-wrap LaTeX math macros (e.g. \dfrac{5}{2}) in choices option if missing $
                    if (/\\(dfrac|frac|sqrt|cdot|times|pm|le|ge|ne|in|vec|mathbf|boldsymbol|mathrm|text|alpha|beta|gamma|delta|theta|pi|varphi|omega)\b/.test(cleanItem) && !/\$/.test(cleanItem)) {
                        cleanItem = '$' + cleanItem + '$';
                    }
                    const imageOnly = /^!\[[^\]]*\]\([^)]+\)$/.test(item.trim());
                    html += `<div class="choices-item ${imageOnly ? 'choices-image-item' : ''} flex items-baseline"><span class="choices-label font-bold mr-1.5 text-slate-800 shrink-0">${label}.</span><div class="choices-content flex-1 [&>p]:m-0 [&>p]:inline">${cleanItem}</div></div>`;
                });
                html += '</div>';
                return html;
            });

            function splitLatexTableParts(source, mode) {
                const parts = [];
                let current = "";
                let braceDepth = 0;
                for (let i = 0; i < source.length; i++) {
                    const ch = source[i];
                    const next = source[i + 1];
                    if (ch === "\\") {
                        if (mode === "rows" && braceDepth === 0 && next === "\\") {
                            parts.push(current);
                            current = "";
                            i++;
                            continue;
                        }
                        if (mode === "rows" && braceDepth === 0 && source.slice(i, i + 3) === "\\cr") {
                            parts.push(current);
                            current = "";
                            i += 2;
                            continue;
                        }
                        current += ch;
                        if (i + 1 < source.length) {
                            current += source[i + 1];
                            i++;
                        }
                        continue;
                    }
                    if (ch === "{") braceDepth++;
                    else if (ch === "}" && braceDepth > 0) braceDepth--;
                    if (mode === "cells" && ch === "&" && braceDepth === 0) {
                        parts.push(current);
                        current = "";
                    } else {
                        current += ch;
                    }
                }
                parts.push(current);
                return parts;
            }

            function readLatexGroup(source, startIndex) {
                if (source[startIndex] !== "{") return null;
                let depth = 0;
                for (let i = startIndex; i < source.length; i++) {
                    if (source[i] === "\\" && i + 1 < source.length) {
                        i++;
                        continue;
                    }
                    if (source[i] === "{") depth++;
                    else if (source[i] === "}") {
                        depth--;
                        if (depth === 0) {
                            return { value: source.slice(startIndex + 1, i), end: i + 1 };
                        }
                    }
                }
                return null;
            }

            function parseLeadingLatexCommand(source, command, groupCount) {
                const trimmed = source.trim();
                const prefix = "\\" + command;
                if (!trimmed.startsWith(prefix)) return null;
                let cursor = prefix.length;
                const groups = [];
                for (let i = 0; i < groupCount; i++) {
                    while (/\s/.test(trimmed[cursor] || "")) cursor++;
                    const group = readLatexGroup(trimmed, cursor);
                    if (!group) return null;
                    groups.push(group.value);
                    cursor = group.end;
                }
                return { groups: groups, remainder: trimmed.slice(cursor).trim() };
            }

            function unescapeTableCellForHtml(value) {
                return value.replace(/\\textbackslash\{\}/g, "&#92;")
                            .replace(/\\textdollar\{\}/g, "$")
                            .replace(/\\textasciicircum\{\}/g, "^")
                            .replace(/\\textasciitilde\{\}/g, "~")
                            .replace(/\\&/g, "&amp;")
                            .replace(/\\%/g, "%")
                            .replace(/\\#/g, "#")
                            .replace(/\\_/g, "_")
                            .replace(/\\\{/g, "{")
                            .replace(/\\\}/g, "}");
            }

            // Process tabular environments with brace-aware cell splitting and
            // native colspan/rowspan support for Word merged cells.
            tempText = tempText.replace(/\\begin\{tabular\*?\}\s*\{([^}]*?)\}([\s\S]*?)\\end\{tabular\*?\}/g, function(match, colSpec, inner) {
                const alignList = [];
                let colIdx = 0;
                for (let i = 0; i < colSpec.length; i++) {
                    const ch = colSpec[i].toLowerCase();
                    if (ch === "l" || ch === "c" || ch === "r" || ch === "p" || ch === "m") {
                        alignList[colIdx] = ch === "l" ? "text-left" : (ch === "r" ? "text-right" : "text-center");
                        colIdx++;
                    }
                }

                const normalizedInner = inner.replace(/\\\\\[[^\]]*?\]/g, "\\\\");
                const rawRows = splitLatexTableParts(normalizedInner, "rows");
                let activeRowspans = [];
                let html = '<div class="overflow-x-auto my-3 max-w-full text-center select-none"><table class="inline-table mx-auto text-xs text-slate-700 dark:text-slate-200 border-collapse border border-slate-300 dark:border-slate-600 bg-slate-50/60 dark:bg-slate-800/40 rounded-lg shadow-sm"><tbody>';

                rawRows.forEach((rowStr) => {
                    const trimmed = rowStr.trim();
                    if (!trimmed) return;

                    const isTopRule = /\\toprule/.test(trimmed);
                    const isMidRule = /\\midrule/.test(trimmed);
                    const isBottomRule = /\\bottomrule/.test(trimmed);
                    const cleanRow = trimmed.replace(/\\(?:hline|toprule|midrule|bottomrule)\b/g, "")
                                            .replace(/\\cline\s*\{[^}]*?\}/g, "")
                                            .trim();
                    if (!cleanRow) return;

                    let rowClass = "border-b border-slate-300 dark:border-slate-700 hover:bg-slate-100/40 dark:hover:bg-slate-700/30 transition-colors";
                    if (isTopRule) rowClass += " border-t-2 border-t-slate-800 dark:border-t-slate-200";
                    if (isMidRule) rowClass += " border-b-2 border-b-slate-600 dark:border-b-slate-400";
                    if (isBottomRule) rowClass += " border-b-2 border-b-slate-800 dark:border-b-slate-200";

                    html += '<tr class="' + rowClass + '">';
                    const rawCells = splitLatexTableParts(cleanRow, "cells");
                    const nextActiveRowspans = activeRowspans.map((count) => Math.max(0, count - 1));
                    let currentCol = 0;

                    rawCells.forEach((cellStr) => {
                        let cell = cellStr.trim();
                        let colspan = 1;
                        let rowspan = 1;
                        let alignClass = alignList[currentCol] || "text-center";

                        const multicolumn = parseLeadingLatexCommand(cell, "multicolumn", 3);
                        if (multicolumn) {
                            colspan = parseInt(multicolumn.groups[0], 10) || 1;
                            const spec = multicolumn.groups[1].toLowerCase();
                            if (spec.includes("l")) alignClass = "text-left";
                            else if (spec.includes("r")) alignClass = "text-right";
                            else alignClass = "text-center";
                            cell = (multicolumn.groups[2] + multicolumn.remainder).trim();
                        }

                        const multirow = parseLeadingLatexCommand(cell, "multirow", 3);
                        if (multirow) {
                            rowspan = parseInt(multirow.groups[0], 10) || 1;
                            cell = (multirow.groups[2] + multirow.remainder).trim();
                        }

                        let coveredByPriorRow = true;
                        for (let offset = 0; offset < colspan; offset++) {
                            if (!(activeRowspans[currentCol + offset] > 0)) {
                                coveredByPriorRow = false;
                                break;
                            }
                        }
                        if (!cell && coveredByPriorRow) {
                            currentCol += colspan;
                            return;
                        }

                        if (rowspan > 1) {
                            for (let offset = 0; offset < colspan; offset++) {
                                nextActiveRowspans[currentCol + offset] = rowspan - 1;
                            }
                        }

                        cell = normalizeNakedMathForPreview(unescapeTableCellForHtml(cell));
                        const borderClass = "border border-slate-300 dark:border-slate-700";
                        const rowspanAttr = rowspan > 1 ? ' rowspan="' + rowspan + '"' : "";
                        html += '<td colspan="' + colspan + '"' + rowspanAttr + ' class="px-3 py-1.5 ' + borderClass + ' ' + alignClass + ' font-normal align-middle">' + cell + '</td>';
                        currentCol += colspan;
                    });

                    activeRowspans = nextActiveRowspans;
                    html += '</tr>';
                });

                html += '</tbody></table></div>';
                return html;
            });

            tempText = tempText.replace(/\\begin\{center\}/g, '<div class="text-center my-1">')
                               .replace(/\\end\{center\}/g, '</div>')
                               .replace(/\\item\s*\[([^\]]+?)\]/g, '</li><li class="my-0.5 list-none -ml-4">$1 ')
                               .replace(/\\item/g, '</li><li class="my-0.5">')
                               .replace(/\\begin\{itemize\}/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/\\begin\{enumerate\}/g, '<ol class="list-decimal pl-4 my-1">')
                               .replace(/\\end\{itemize\}/g, '</li></ul>')
                               .replace(/\\end\{enumerate\}/g, '</li></ol>')
                               .replace(/<ul class="list-disc pl-4 my-1">\s*<\/li>/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/<ol class="list-decimal pl-4 my-1">\s*<\/li>/g, '<ol class="list-decimal pl-4 my-1">');

            // Process LaTeX bold formatting outside math environments
            tempText = tempText.replace(/\\textbf\s*\{([^{}]*?)\}/g, '<strong>$1</strong>');

            // Replace spacing commands outside math blocks with non-breaking spaces for a clean sidebar preview
            tempText = tempText.replace(/\\\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\\\quad/g, '&nbsp;&nbsp;')
                               .replace(/\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\quad/g, '&nbsp;&nbsp;')
                               .replace(/(\\+) /g, function(match, slashes) {
                                   // Only an unpaired final slash is a TeX space;
                                   // paired slashes remain available as hard breaks.
                                   return slashes.length % 2 === 0 ? match : slashes.slice(0, -1) + '&nbsp;';
                               });
                               
            // Replace LaTeX line breaks with HTML br tags outside math environments
            tempText = tempText.replace(/\\\\/g, '<br>');
            
            // A paragraph starts a new line with a short visual gap; two BRs
            // would additionally create a whole empty text line in Word answers.
            const paragraphBreak = '<span class="mb-preview-paragraph-break" aria-hidden="true"></span>';
            // Keep subquestions on distinct paragraphs without adding a second
            // gap when the source already has a blank line or a hard break.
            tempText = tempText.replace(/(?:\r?\n|\s+|([。；;!！\.])\s*)([(（]?(?:[1-9]|10|[ivxIVX]+|[①②③④⑤⑥⑦⑧⑨⑩])[)）\.]|\([1-9]\)|（[1-9]）|\([ivxIVX]+\)|（[ivxIVX]+）)(?=\s*[\u4e00-\u9fa5a-zA-Z\$])/g, function(match, punctuation, marker, offset, source) {
                const beforeMarker = source.slice(0, offset + match.length - marker.length);
                // The optional opening bracket must not let a suffix such as
                // 5) in (6.9.5), or 8. at the end of 2, 4, 6, 8., become a
                // subquestion. These are continuous prose in the source.
                if (!/^[(（]/.test(marker)
                        && /(?:\d[.．]\s*|\d\s*[,，]\s*)$/.test(beforeMarker)) return match;
                return (punctuation || '') + paragraphBreak + marker + ' ';
            });

            // 双回车起新段落；单回车仅视为空格；显式 \\\\ 保留为硬换行。
            tempText = tempText.replace(/\r\n/g, '\n')
                               .replace(/\n\n+/g, paragraphBreak)
                               .replace(/\n/g, ' ');
                               
            // 转换 Markdown 题目插图与配图语法 ![](/static/uploads/xxx.png) 为精美自适应预览图
            tempText = tempText.replace(/!\[(.*?)\]\(([^)]+)\)/g, function(match, alt, src) {
                const safeSrc = window.MathBankSafe.safeImageUrl(src);
                if (!safeSrc) return '';
                const safeAlt = window.MathBankSafe.escapeAttribute(alt || '题目配图');
                const key = window.ImageLayoutTools ? window.ImageLayoutTools.key(src) : src;
                const layout = Object.prototype.hasOwnProperty.call(imageLayouts || {}, key) ? imageLayouts[key] : null;
                const align = layout && ['left', 'center', 'right'].includes(layout.align) ? layout.align : 'center';
                const size = layout && ['auto', 'small', 'medium', 'large'].includes(layout.size) ? layout.size : 'auto';
                return `<div class="my-2.5 text-center mb-inline-image-align-${align}"><img src="${window.MathBankSafe.escapeAttribute(safeSrc)}" alt="${safeAlt}" class="mb-inline-image-size-${size} max-w-[220px] max-h-[180px] object-contain rounded-lg border border-slate-200 shadow-sm inline-block cursor-zoom-in hover:shadow-sm hover:scale-[1.02] transition-all" data-safe-image-open="true" title="点击在新标签页查看高清原图"></div>`;
            });

            // Generated block content already owns its vertical spacing. Keep
            // image/choice/table anchors and display math in place, but do not
            // stack a paragraph spacer on their existing block margins.
            tempText = tempText
                .replace(new RegExp('(?:' + paragraphBreak + '\\s*){2,}', 'g'), paragraphBreak)
                .replace(new RegExp('(?:<br>\\s*)+' + paragraphBreak, 'g'), paragraphBreak)
                .replace(new RegExp(paragraphBreak + '(?:\\s*<br>)+', 'g'), paragraphBreak)
                .replace(new RegExp(paragraphBreak + '\\s*(?=<(?:div|table|ul|ol)\\b)', 'g'), '')
                .replace(new RegExp('(</(?:div|table|ul|ol)>)\\s*' + paragraphBreak, 'g'), '$1')
                .replace(new RegExp('^(?:\\s*' + paragraphBreak + ')+|(?:' + paragraphBreak + '\\s*)+$', 'g'), '');
            placeholders.forEach(({placeholder, original}) => {
                if (!/^(?:\$\$|\\\[)/.test(original)) return;
                tempText = tempText
                    .replace(new RegExp(paragraphBreak + '\\s*' + placeholder, 'g'), placeholder)
                    .replace(new RegExp(placeholder + '\\s*' + paragraphBreak, 'g'), placeholder);
            });
            markdownTables.tables.forEach(({marker}) => {
                tempText = tempText
                    .replace(new RegExp(paragraphBreak + '\\s*' + marker, 'g'), marker)
                    .replace(new RegExp(marker + '\\s*' + paragraphBreak, 'g'), marker);
            });
                               
            // Restore math blocks with HTML escaping
            function escapeHtml(str) {
                return str.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;');
            }

            // Adjacent inline math such as $a$$b$$c$ can make a later
            // placeholder contain an earlier one. Restore in LIFO order so
            // the outer placeholder is expanded before its nested token.
            placeholders.slice().reverse().forEach(({ placeholder, original }) => {
                tempText = tempText.replace(placeholder, () => escapeHtml(original));
            });
            literalEntries.forEach(({marker, visible}) => {
                // DOMPurify allows code, and every KaTeX auto-render entrance
                // ignores code by default. Keep the contents inert and exact.
                const html = '<code class="mb-preview-literal">' + escapeHtml(visible) + '</code>';
                tempText = tempText.split(marker).join(html);
            });

            // A table owns its rows, so prose line folding and subquestion
            // inference above cannot alter its cells. Each cell still uses
            // the same formula, image and literal protections. The final
            // parseMarkdownWithMath call sanitizes the complete result.
            markdownTables.tables.forEach(table => {
                const renderRow = (cells, tag) => '<tr>' + cells.map((cell, index) => {
                    const weight = tag === 'th' ? 'font-semibold' : 'font-normal';
                    return '<' + tag + ' class="px-3 py-1.5 border border-slate-300 dark:border-slate-700 '
                        + table.align[index] + ' ' + weight + ' align-middle whitespace-nowrap">'
                        + preprocessFormulaForKaTeX(cell, imageLayouts) + '</' + tag + '>';
                }).join('') + '</tr>';
                const html = '<div class="mb-markdown-table-preview overflow-x-auto my-3 max-w-full text-center">'
                    + '<table class="inline-table mx-auto border-collapse border border-slate-300 dark:border-slate-600 '
                    + 'bg-slate-50/60 dark:bg-slate-800/40 rounded-lg shadow-sm">'
                    + '<thead>' + renderRow(table.header, 'th') + '</thead><tbody>'
                    + table.rows.map(row => renderRow(row, 'td')).join('') + '</tbody></table></div>';
                tempText = tempText.split(table.marker).join(html);
            });
            
            return tempText;
        }
        window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;
            
        const QuestionReferenceTools = (() => {
            let counter = 0;
            let jumpSequence = 0;
            const controls = new Map();
            function prepare(text, context) {
                const source = String(text || '');
                if (!context || context.source !== source || !Array.isArray(context.references)) {
                    return {source, entries: []};
                }
                const chars = Array.from(source);
                // Even a corrupt/imported relation must never turn code, a URL,
                // an image path or a mathematical expression into a jump control.
                let prose = replacePreviewLiteralSpans(source, value => ' '.repeat(value.length));
                prose = replaceDelimitedMathForPreview(prose, value => ' '.repeat(value.length));
                prose = replaceLatexEnvironmentsForPreview(prose, (value, name) =>
                    /^(?:tikzpicture|math|displaymath|equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|array|cases|aligned|gathered)$/.test(name)
                        ? ' '.repeat(value.length) : value);
                const groups = [], groupsBySpan = new Map();
                let lastEnd = -1;
                context.references.slice().sort((a, b) => Number(a.start) - Number(b.start)).forEach(reference => {
                    const start = reference.start, end = reference.end;
                    const spanKey = start + ':' + end;
                    const existing = groupsBySpan.get(spanKey);
                    if (reference.field !== context.field || !Number.isSafeInteger(start)
                            || !Number.isSafeInteger(end) || start < 0 || end <= start || end > chars.length
                            || (start < lastEnd && !existing) || typeof reference.text !== 'string'
                            || chars.slice(start, end).join('') !== reference.text
                            || prose.slice(chars.slice(0, start).join('').length,
                                chars.slice(0, end).join('').length) !== reference.text) return;
                    const prefix = String(reference.prefix || ''), suffix = String(reference.suffix || '');
                    if (prefix && chars.slice(Math.max(0, start - Array.from(prefix).length), start).join('') !== prefix) return;
                    if (suffix && chars.slice(end, end + Array.from(suffix).length).join('') !== suffix) return;
                    const target = Number(reference.target_question_id), seq = Number(reference.seq_num);
                    const resolved = reference.status === 'resolved' && Number.isSafeInteger(target) && target > 0
                        && Number.isSafeInteger(seq) && seq > 0;
                    if (!resolved && !['target_deleted', 'unresolved'].includes(reference.status)) return;
                    const member = {reference, target, seq, resolved};
                    if (existing) {
                        if (!existing.members.some(item => item.reference.label_key === reference.label_key)) existing.members.push(member);
                    } else {
                        const group = {
                            reference, members: [member], isCurrent: context.isCurrent,
                            sourceQuestionId: context.sourceQuestionId,
                            originalSource: typeof context.originalSource === 'string' ? context.originalSource : source
                        };
                        groups.push(group); groupsBySpan.set(spanKey, group); lastEnd = end;
                    }
                });
                const entries = [];
                groups.forEach(group => {
                    group.isRange = group.members.length > 1 || group.reference.is_range === true
                        || (Array.isArray(group.reference.candidate_label_keys) && group.reference.candidate_label_keys.length > 1);
                    if (!group.isRange && !group.members[0].resolved
                            && group.members[0].reference.status !== 'target_deleted') return;
                    let marker;
                    do { marker = '\uE010' + (++counter) + '\uE011'; } while (source.includes(marker));
                    entries.push({...group, marker, ...group.members[0], members: group.members});
                });
                entries.slice().reverse().forEach(entry => { chars.splice(entry.reference.end, 0, entry.marker); });
                return {source: chars.join(''), entries};
            }
            function appendControls(html, entries) {
                if (!entries.length) return html;
                const template = document.createElement('template');
                template.innerHTML = html;
                const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
                const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
                nodes.forEach(node => {
                    const matches = entries.filter(entry => node.nodeValue.includes(entry.marker));
                    if (!matches.length) return;
                    const fragment = document.createDocumentFragment();
                    let remaining = node.nodeValue;
                    while (matches.length) {
                        matches.sort((a, b) => remaining.indexOf(a.marker) - remaining.indexOf(b.marker));
                        const entry = matches.shift(), index = remaining.indexOf(entry.marker);
                        fragment.appendChild(document.createTextNode(remaining.slice(0, index)));
                        // Controls are created only after the normal rich-HTML
                        // sanitizer. Its allowed tags/attributes remain unchanged.
                        if (!node.parentElement || !node.parentElement.closest('code,pre,a,.katex')) {
                            entry.members.forEach((member, memberIndex) => {
                                const label = String(member.reference.label_key || member.reference.text);
                                const unavailable = member.reference.status === 'target_deleted' ? '目标已删除' : '待关联';
                                const control = document.createElement(member.resolved ? 'button' : 'span');
                                control.className = 'mb-question-reference' + (member.resolved ? '' : ' is-unavailable');
                                control.textContent = (entry.isRange ? label + '：' : '')
                                    + (member.resolved ? '题库 #' + member.seq : unavailable);
                                if (member.resolved) {
                                    control.type = 'button';
                                    control.setAttribute('aria-label', '打开' + label + '对应的题库题目 #' + member.seq);
                                    const key = entry.marker.slice(1, -1) + '-' + memberIndex;
                                    control.setAttribute('data-question-reference-key', key);
                                    controls.set(key, {...member, isCurrent: entry.isCurrent,
                                        sourceQuestionId: entry.sourceQuestionId, originalSource: entry.originalSource});
                                } else control.setAttribute('aria-label', label + '对应的题目' + unavailable);
                                fragment.appendChild(control);
                            });
                        }
                        remaining = remaining.slice(index + entry.marker.length);
                    }
                    fragment.appendChild(document.createTextNode(remaining));
                    node.replaceWith(fragment);
                });
                // Detached previews should not keep an unbounded registry alive.
                // Keep controls that are still on screen even in a very large
                // paper; dropping an arbitrary oldest key would disable it.
                if (controls.size > 4096) {
                    const retained = new Set(Array.from(document.querySelectorAll('button[data-question-reference-key]'))
                        .map(button => button.getAttribute('data-question-reference-key')));
                    entries.forEach(entry => entry.members.forEach((_, index) => retained.add(entry.marker.slice(1, -1) + '-' + index)));
                    for (const key of controls.keys()) if (!retained.has(key)) controls.delete(key);
                }
                return template.innerHTML;
            }
            if (typeof document !== 'undefined') document.addEventListener('click', async event => {
                const button = event.target && event.target.closest
                    ? event.target.closest('button[data-question-reference-key]') : null;
                if (!button) return;
                const entry = controls.get(button.getAttribute('data-question-reference-key'));
                if (!entry) return;
                event.preventDefault(); event.stopPropagation();
                if (typeof entry.isCurrent === 'function' && !entry.isCurrent()) {
                    showToast('引用信息已变化，请稍候或保存后再查看', 'info');
                    return;
                }
                const sourceId = Number(entry.sourceQuestionId), sequence = ++jumpSequence;
                if (!Number.isSafeInteger(sourceId) || sourceId <= 0) return;
                const session = EditorState.snapshot();
                try {
                    // Read the owner's current binding before following a stable
                    // ID. External deletion/import can reuse an SQLite ID without
                    // this page having received a mutation/focus event yet.
                    const response = await fetch(`/api/questions/${sourceId}`);
                    if (!response.ok) throw new Error('引用来源题目已失效');
                    const owner = await response.json();
                    if (sequence !== jumpSequence || !EditorState.isCurrent(session)
                            || (typeof entry.isCurrent === 'function' && !entry.isCurrent())) return;
                    const reference = entry.reference;
                    const current = (owner.question_references || []).find(item => item.field === reference.field
                        && item.start === (reference.original_start == null ? reference.start : reference.original_start)
                        && item.end === (reference.original_end == null ? reference.end : reference.original_end)
                        && item.book_key === reference.book_key && item.label_key === reference.label_key);
                    if (Number(owner.id) !== sourceId || String(owner[reference.field] || '') !== entry.originalSource
                            || !current || current.status !== 'resolved'
                            || Number(current.target_question_id) !== entry.target) {
                        window.dispatchEvent(new Event('mathbank-question-mutated'));
                        showToast('此处引用已变化，已刷新引用信息，请重新查看', 'info');
                        return;
                    }
                } catch (_) {
                    showToast('无法核对引用题目，请稍候再试', 'error');
                    return;
                }
                selectQuestionById(entry.target);
            }, true);
            return Object.freeze({prepare, appendControls});
        })();
        window.QuestionReferenceTools = QuestionReferenceTools;

        function parseMarkdownWithMath(text, imageLayouts = {}, referenceContext = null) {
            if (!text) return "";
            if (!referenceContext) {
                return window.MathBankSafe.sanitizeRichHtml(preprocessFormulaForKaTeX(text, imageLayouts));
            }
            const prepared = QuestionReferenceTools.prepare(text, referenceContext);
            const html = window.MathBankSafe.sanitizeRichHtml(preprocessFormulaForKaTeX(prepared.source, imageLayouts));
            return QuestionReferenceTools.appendControls(html, prepared.entries);
        }
        window.parseMarkdownWithMath = parseMarkdownWithMath;

        function renderQuestionPreviewContent(container, text, options = {}) {
            if (!container) return;
            const settings = options && typeof options === 'object' ? options : {};
            const includeImages = settings.includeImages !== false;
            let preparedHtml;
            if (typeof settings.preparedHtml === 'string' && !settings.referenceContext) {
                preparedHtml = window.MathBankSafe.sanitizeRichHtml(settings.preparedHtml);
                if (!includeImages) {
                    preparedHtml = preparedHtml.replace(/<img\b[^>]*>/gi, '');
                }
            } else {
                let source = String(text || '');
                if (!includeImages) {
                    source = source
                        .replace(/!\[[^\]\n]*\]\(\s*(?:<[^>\n]*>|[^\s)]+)(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)/gi, '')
                        .replace(/<img\b[^>]*>/gi, '');
                }
                if (settings.referenceContext) {
                    preparedHtml = parseMarkdownWithMath(source, settings.imageLayouts || {}, settings.referenceContext);
                } else {
                    preparedHtml = parseMarkdownWithMath(source, settings.imageLayouts || {});
                }
            }
            container.innerHTML = preparedHtml;
            try {
                renderMathInElement(container, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false},
                        {left: '\\(', right: '\\)', display: false},
                        {left: '\\[', right: '\\]', display: true}
                    ],
                    throwOnError: false
                });
            } catch (error) {
                console.error('KaTeX question preview rendering error: ', error);
            }
            adaptChoicesGridLayout(container);
            return preparedHtml;
        }
        window.renderQuestionPreviewContent = renderQuestionPreviewContent;

        // Format raw OCR questions by detecting choice options and introducing nice line breaks
        function formatQuestionContent(text) {
            if (!text) return "";
            
            // Strip the LaTeX negative space command "\!" and thin space "\," which are cluttering
            let formatted = text.replace(/\\!/g, '').replace(/\\,/g, '');
            
            // 1. Check if it is actually a choice question with options A, B, C, D
            const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const isChoiceQuestion = hasA && hasB && hasC && hasD;
            
            // Protect math blocks from being replaced, and clean up formula-level exclamation noise
            const parts = formatted.split(/(\$\$[\s\S]*?\$\$|\$[^\$]+?\$)/g);
            for (let i = 0; i < parts.length; i++) {
                // If it is a math block (odd indices in split result)
                if (i % 2 === 1) {
                    // 1. Remove all Chinese full-width exclamation marks "！" inside math
                    parts[i] = parts[i].replace(/！/g, '');
                    // 2. Remove all standalone half-width exclamation marks "!" not preceded by numbers or letters
                    parts[i] = parts[i].replace(/(^|[^0-9a-zA-Z\)\}\]])!/g, '$1');
                    // 3. Remove any remaining negative spacing "\!" just in case
                    parts[i] = parts[i].replace(/\\!/g, '');
                    // 4. Remove all LaTeX thin spaces "\,"
                    parts[i] = parts[i].replace(/\\,/g, '');
                } else {
                    // Only modify non-math blocks (even indices in split result)
                    if (isChoiceQuestion) {
                        // Replace option labels with clean newlines and normalized format
                        parts[i] = parts[i]
                            .replace(/[\s,，、]*\b([A-D])(?:[\.\s、，．]+)(?!\$)/g, '\n\n$1. ')
                            .replace(/[\s,，、]*\(([A-D])\)(?!\$)/g, '\n\n($1) ')
                            .replace(/[\s,，、]*（([A-D])）(?!\$)/g, '\n\n($1) ');
                    }
                }
            }
            formatted = parts.join('');
            
            // Clean up any leading/trailing duplicate newlines
            formatted = formatted.replace(/\n{3,}/g, '\n\n').trim();
            
            return formatted;
        }

        // 🟢 智能心跳循环：每 15 秒向后台发送一次轻量级心跳
        // 只要题库页面（有任意标签页）处于打开状态，后台的 1 小时闲置自杀机制就不会被触发
        setInterval(() => {
            fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
        }, 15000);
