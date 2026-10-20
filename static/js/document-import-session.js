// Durable browser-side corrections are separate from the ordinary editor drafts.
// Restoring this record only reads task status; a paid continuation needs a click.
(() => {
    'use strict';
    const STORAGE_KEY = 'mathbank_document_import_session_v1';
    const fields = {
        content: 'content-textarea', answer_markdown: 'answer-textarea',
        question_type: 'qtype', difficulty: 'difficulty', source: 'source',
        category_compulsory: 'compulsory', category_chapter: 'chapter', category_knowledge: 'knowledge'
    };
    const clone = value => JSON.parse(JSON.stringify(value));
    let state = null;
    let saveTimer = null;
    let restored = false;
    let restoring = false;
    let busy = false;
    let storageWarningShown = false;
    let serverSaveTimer = null;
    let serverSaveInFlight = null;
    let serverSaveProjectId = null;
    let serverSaveAgain = false;
    let sessionEpoch = 0;
    let projectList = [];
    let projectListLoading = false;
    const deletingProjects = new Set();
    const deletedProjects = new Set();
    const pendingProjectRequests = new Map();
    let sourcePagesLoading = false;
    let lastServerDraft = '';
    let editSerial = 0;
    const restoredCards = new WeakSet();
    const element = id => document.getElementById(id);
    const validId = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,160}$/.test(value);

    const pdfCloudOnly = () => window.__pdfCloudOnly === true;
    function transientPdf(value = state) {
        const type = value?.documentType || value?.document_type;
        return pdfCloudOnly() && (type === 'pdf' || value?.transientPdf === true
            || (!type && validId(value?.projectId)));
    }

    function useTransientPdf() {
        if (!transientPdf()) return false;
        state.transientPdf = true;
        // Existing PDF archives remain on disk, but this session has no
        // project identity or durable correction/resume contract.
        ['projectId', 'projectRevision', 'projectRevisionReceipt', 'projectSaveStatus', 'projectError',
            'projectLocalDirty', 'projectSourcePageCount', 'expiresAt', 'lastUsedAt', 'totalBytes', 'page_edits']
            .forEach(key => delete state[key]);
        state.canResume = false;
        return true;
    }

    function hidePdfProjectPanels() {
        ['documentProjectsPanel', 'documentImportRecoveryPanel', 'documentProjectPageEditor']
            .forEach(id => { const panel = element(id);if (panel) panel.hidden = true; });
    }

    function questionsSnapshot() {
        return parsedQuestionsData.map((question, index) => {
            const snapshot = clone(question);
            const card = element(`parsed-card-${index}`);
            if (card?.__sourceQuestion === question) {
                snapshot.document_import_fields_saved = true;
                Object.entries(fields).forEach(([name, selector]) => {
                    const input = card.querySelector(`.card-${selector}`);
                    if (input) snapshot[name] = input.value;
                });
                const checkbox = card.querySelector('.card-select-checkbox');
                if (checkbox) snapshot.document_import_selected = checkbox.checked;
            }
            if (typeof parsedSourceReviewSnapshots !== 'undefined' && parsedSourceReviewSnapshots.has(question)
                    && parsedSourceReviewSnapshots.get(question) !== parsedSourceReviewSnapshot(index, question)) {
                snapshot.document_import_source_edited = true;
            }
            return snapshot;
        });
    }

    function flush() {
        if (restoring || !state) return;
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = null;
        state.questions = questionsSnapshot();
        state.paperTitle = element('importPaperTitle')?.value || '';
        state.pageImages = Array.isArray(window.pdfPageImages) ? window.pdfPageImages.slice() : [];
        state.pageNumbers = Array.isArray(window.pdfPageNumbers) ? window.pdfPageNumbers.slice() : [];
        state.diagnostics = typeof parsedSourceReviewReport !== 'undefined' && parsedSourceReviewReport
            ? clone(parsedSourceReviewReport) : state.diagnostics;
        state.updatedAt = Date.now();
        if (useTransientPdf()) return;
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
            if (validId(state.projectId)) localStorage.setItem(STORAGE_KEY + ':' + state.projectId, JSON.stringify(state));
        } catch (error) {
            if (!storageWarningShown) {
                storageWarningShown = true;
                showToast('导入会话暂存失败，当前浏览器空间不足或禁止存储；请先完成入库再刷新。', 'warning');
            }
        }
        scheduleServerSave();
    }

    function saveSoon() {
        if (restoring) return;
        if (!state && parsedQuestionsData.length) state = { version: 1, phase: 'completed', questions: [], baselineQuestions: [] };
        if (!state) return;
        if (!useTransientPdf() && validId(state.projectId)) {
            editSerial += 1;state.projectLocalDirty = true;
            if (state.projectSaveStatus !== 'conflict') state.projectSaveStatus = 'local';
        }
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(flush, 200);
    }

    function begin(metadata) {
        // Rendering the first cards may have scheduled a snapshot before the
        // upload metadata arrived. Bind that untyped snapshot before flushing
        // so entering PDF mode cannot persist it as a generic document draft.
        if (state && transientPdf(metadata) && !state.documentType && !validId(state.projectId)) {
            state.documentType = 'pdf';
        }
        if (state) flush();
        sessionEpoch += 1;
        if (serverSaveTimer) clearTimeout(serverSaveTimer);
        serverSaveTimer = null;
        lastServerDraft = '';
        restored = false;busy = false;restoring = false;
        state = { version: 1, questions: [], baselineQuestions: clone(parsedQuestionsData), ...metadata };
        useTransientPdf();
        flush();
        renderRecovery();
    }

    function update(metadata) {
        if (!state) state = { version: 1, questions: [], baselineQuestions: [] };
        acknowledgeInitialRevision(metadata);
        Object.entries(metadata).forEach(([key, value]) => {
            if (value === undefined) return;
            if (key === 'projectRevisionReceipt') return;
            if (key === 'projectRevision' && state.projectRevision !== undefined && state.projectId === metadata.projectId) return;
            state[key] = value;
        });
        useTransientPdf();
        persistOnly();
        renderRecovery();
        const incomplete = typeof pdfStructureReviewState === 'function' && pdfStructureReviewState(state.diagnostics, state).incomplete;
        if (validId(state.projectId) && (incomplete || ['error', 'partial', 'interrupted'].includes(state.phase))
                && !state.questions?.length && !state.page_edits?.length) loadProjectSourcePages();
    }

    function acknowledgeInitialRevision(metadata) {
        const receipt = metadata.projectRevisionReceipt;
        // A task status can report a revision saved by a different window.
        // Only the server's still-current first machine draft has authority to
        // acknowledge the upload's 0-to-1 transition without replacing edits.
        if (!receipt || receipt.schema !== 'mathbank.document-project-initial-revision.v1'
                || receipt.origin !== 'first_machine_draft'
                || receipt.project_id !== state?.projectId || metadata.projectId !== state.projectId
                || !validId(receipt.task_id) || receipt.task_id !== state.taskId || receipt.task_id !== metadata.taskId
                || state.projectRevision !== 0 || metadata.projectRevision !== 1
                || receipt.base_revision !== 0 || receipt.revision !== 1
                || typeof receipt.draft_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(receipt.draft_sha256)) return false;
        state.projectRevision = 1;
        state.projectRevisionReceipt = clone(receipt);
        if (state.projectSaveStatus === 'conflict') {
            state.projectSaveStatus = state.projectLocalDirty ? 'local' : 'saved';
            state.projectError = '';
        }
        return true;
    }

    function clear(options = {}) {
        let keepPdfArchive = transientPdf();
        if (pdfCloudOnly() && !keepPdfArchive) {
            try { keepPdfArchive = transientPdf(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')); }
            catch (error) { /* Preserve unrelated malformed archives under the ordinary rules. */ }
        }
        if (validId(state?.projectId) && options.discardDeletedProject !== true) {flush();backupUnsynced();}
        sessionEpoch += 1;
        if (serverSaveTimer) clearTimeout(serverSaveTimer);
        serverSaveTimer = null;lastServerDraft = '';
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = null;
        state = null;
        restored = false;busy = false;restoring = false;
        if (!keepPdfArchive) {
            try { localStorage.removeItem(STORAGE_KEY); } catch (error) { /* A blocked store also cannot be restored. */ }
        }
        renderRecovery();renderPageEditor();
    }

    function itemId(question) {
        return question?.project_card_id || question?.import_item_id || question?.source_item_id || '';
    }

    const metadataFields = ['question_type', 'difficulty', 'category_compulsory', 'category_chapter', 'category_knowledge'];

    function mergeKnowledgePointSuggestions(current, incoming) {
        const labels = window.KnowledgePointState.normalize(current);
        const codes = new Set(labels.map(item => item.code));
        return [...labels, ...window.KnowledgePointState.normalize(incoming)
            .filter(item => !codes.has(item.code)).map(item => ({...item, status: 'confirmed'}))];
    }

    function workingCopyRestore(value) {
        return value?.diagnostics?.pdf_draft_restore?.working_copy === true
            || value?.task?.diagnostics?.pdf_draft_restore?.working_copy === true;
    }

    function localDraftMode() {
        return Boolean(state?.diagnostics?.pdf_local_draft);
    }

    function sourceSnapshot(question) {
        return JSON.stringify([
            question?.content || '', question?.answer_markdown || '', question?.source || '',
            question?.source_number ?? null, question?.image_paths || [], question?.referenced_images || [],
            question?.content_tikz_assets || [], question?.answer_tikz_assets || [],
            question?.tikz_code || '', question?.tikz_reference_image_path || '',
            question?.image_layouts || {}, question?.figure_align || '', question?.figure_size || '',
            question?.figure_align_custom === true
        ]);
    }

    function mergeMetadata(current, original, incoming) {
        const result = clone(current);
        const path = ['category_compulsory', 'category_chapter', 'category_knowledge'];
        const same = field => String(current?.[field] || '') === String(original?.[field] || '');
        const manualPath = current?.classification_review?.confirmed_by === 'user'
            && original?.classification_review?.confirmed_by !== 'user';
        const keepPath = !original || manualPath || !path.every(same);
        const keepPoints = !original || JSON.stringify(current.knowledge_points || []) !== JSON.stringify(original.knowledge_points || []);
        if (!keepPoints && Array.isArray(incoming.knowledge_points)) {
            result.knowledge_points = mergeKnowledgePointSuggestions(current.knowledge_points, incoming.knowledge_points);
            result.knowledge_point_warnings = clone(incoming.knowledge_point_warnings || []);
        }
        ['question_type', 'difficulty'].forEach(field => {
            if (original && same(field) && incoming[field] !== undefined) result[field] = incoming[field];
        });
        if (!keepPath) {
            path.forEach(field => { if (incoming[field] !== undefined) result[field] = incoming[field]; });
            if (incoming.classification_review) {
                result.classification_review = clone(incoming.classification_review);
                if (current.classification_review?.original) result.classification_review.original = clone(current.classification_review.original);
            } else if (current.classification_review) {
                result.classification_review = {...clone(current.classification_review), required: false,
                    reason_codes: [], message: '分类已匹配。', confirmed_by: 'metadata'};
            }
        }
        const differs = metadataFields.some(field => incoming[field] !== undefined
            && String(result[field] || '') !== String(incoming[field] || ''))
            || (keepPoints && Array.isArray(incoming.knowledge_points)
                && JSON.stringify(result.knowledge_points || []) !== JSON.stringify(incoming.knowledge_points));
        if (differs) {
            result.project_incoming_candidate = {metadata_only: true};
            metadataFields.forEach(field => {
                if (incoming[field] !== undefined) result.project_incoming_candidate[field] = clone(incoming[field]);
            });
            if (incoming.classification_review) result.project_incoming_candidate.classification_review = clone(incoming.classification_review);
            if (Array.isArray(incoming.knowledge_points)) {
                result.project_incoming_candidate.knowledge_points = clone(incoming.knowledge_points);
                result.project_incoming_candidate.knowledge_point_warnings = clone(incoming.knowledge_point_warnings || []);
            }
        } else if (result.project_incoming_candidate?.metadata_only) delete result.project_incoming_candidate;
        if (current.savedSnapshot) {
            result.saved = current.saved === true && correctionSnapshot(current) === correctionSnapshot(result);
            result.modifiedAfterSave = !result.saved;
        }
        return result;
    }

    function mergeCompletion(incoming, options = {}) {
        if ((!restored && !validId(state?.projectId)) || !parsedQuestionsData.length) return incoming;
        const current = questionsSnapshot();
        // A normal resume can acknowledge the already saved teacher draft.
        // It is not a new machine candidate and must not compete with later
        // keystrokes or manufacture another candidate from the working copy.
        if (options.workingCopyRestore === true) return current;
        if (!incoming.length) return current;
        const baseline = Array.isArray(state?.baselineQuestions) ? state.baselineQuestions : [];
        const used = new Set();
        const result = incoming.map(question => {
            const stableId = itemId(question);
            let index = stableId ? current.findIndex(item => itemId(item) === stableId) : -1;
            if (index < 0 && !stableId) {
                const matches = baseline.map((item, position) => item.content === question.content
                    && item.answer_markdown === question.answer_markdown ? position : -1).filter(position => position >= 0);
                if (matches.length === 1) index = matches[0];
            }
            if (index < 0 || index >= current.length || used.has(index)) return question;
            used.add(index);
            if (!validId(state?.projectId)) return current[index];
            const original = stableId ? baseline.find(item => itemId(item) === stableId) : baseline[index];
            // Local rule recovery can rebuild source bodies. Only an
            // unchanged machine source makes this a metadata-only update.
            if (!options.localDraftPreview && state?.diagnostics?.pdf_local_draft?.source_preserved === true
                    && state?.diagnostics?.pdf_draft_metadata && original
                    && sourceSnapshot(question) === sourceSnapshot(original)) {
                return mergeMetadata(current[index], original, question);
            }
            const edited = current[index].document_import_source_edited === true
                || !original || correctionSnapshot(current[index]) !== correctionSnapshot(original);
            if (edited) {
                const retained = current[index];
                if (!options.localDraftPreview && correctionSnapshot(retained) !== correctionSnapshot(question)) {
                    retained.project_incoming_candidate = clone(question);
                }
                return retained;
            }
            const accepted = {...question, document_import_selected: current[index].document_import_selected};
            if (current[index].saved_id) accepted.saved_id = current[index].saved_id;
            if (current[index].savedSnapshot) {
                accepted.savedSnapshot = clone(current[index].savedSnapshot);
                accepted.saved = correctionSnapshot(current[index]) === correctionSnapshot(question) && current[index].saved === true;
                accepted.modifiedAfterSave = !accepted.saved;
            }
            return accepted;
        });
        let retainedUnmatched = 0;
        current.forEach((question, index) => {
            if (used.has(index)) return;
            if (validId(state?.projectId)) {
                const stableId = itemId(question);
                const original = stableId ? baseline.find(item => itemId(item) === stableId) : baseline[index];
                if (original && question.document_import_source_edited !== true
                        && correctionSnapshot(question) === correctionSnapshot(original)) return;
            }
            result.push(question);retainedUnmatched += 1;
        });
        if (retainedUnmatched) {
            showToast('已保留原有校对题卡；部分续跑结果无法唯一对应，请核对新增题卡。', 'warning');
        }
        return result;
    }

    function previewLocalDraft(task) {
        if (!validId(state?.projectId) || task.project_id !== state.projectId || !validId(task.task_id)
                || state.taskId !== task.task_id || state.localDraftPreviewTaskId === task.task_id
                || !task.diagnostics?.pdf_local_draft || !Array.isArray(task.data) || !task.data.length) return false;
        const previousRestoring = restoring;restoring = true;
        try {
            const questions = mergeCompletion(task.data, {localDraftPreview: true});
            state.questions = clone(questions);state.baselineQuestions = clone(task.data);
            state.localDraftPreviewTaskId = task.task_id;
            state.acceptedDiagnostics = clone(task.diagnostics);
            showQuestions(questions);
        } finally { restoring = previousRestoring; }
        persistOnly();renderRecovery();return true;
    }

    function correctionSnapshot(question) {
        const text = ['content', 'answer_markdown', 'review', 'question_type', 'difficulty', 'source',
            'category_compulsory', 'category_chapter', 'category_knowledge', 'tikz_code', 'tikz_reference_image_path'];
        const result = text.map(field => String(question?.[field] || ''));
        ['image_paths', 'content_tikz_assets', 'answer_tikz_assets'].forEach(field => result.push(question?.[field] || []));
        result.push(question?.knowledge_points || []);
        result.push(question?.image_layouts || {}, question?.figure_align || '', question?.figure_size || '',
            question?.figure_align_custom === true);
        // A teacher's classification confirmation is itself a correction even
        // if keeping the whole chapter did not change any dropdown text.
        result.push(question?.classification_review?.confirmed_by === 'user'
            ? question.classification_review.confirmed_classification || true : null);
        return JSON.stringify(result);
    }

    function renderCandidate(card, question, index) {
        const candidate = question?.project_incoming_candidate;
        if (!candidate || typeof candidate !== 'object') return;
        const panel = document.createElement('details');
        panel.className = 'document-project-candidate rounded-lg border border-indigo-200 px-3 py-2 text-[11px] text-slate-600';
        const summary = document.createElement('summary');summary.className = 'cursor-pointer font-semibold';
        const metadataOnly = candidate.metadata_only === true;
        summary.textContent = metadataOnly ? '分类建议 · 当前人工稿已保留' : '新分析结果候选 · 当前人工稿已保留';panel.appendChild(summary);
        const content = document.createElement('pre');content.className = 'whitespace-pre-wrap break-words max-h-60 overflow-auto my-2';
        content.textContent = (metadataOnly ? '' : String(candidate.content || '') + '\n\n答案与解析：\n' + String(candidate.answer_markdown || '') + '\n\n') +
            '分类建议：' + [candidate.category_compulsory, candidate.category_chapter, candidate.category_knowledge].filter(Boolean).join(' / ')
            + '\n知识点建议：' + (candidate.knowledge_points || []).map(item => item.name || item.code).join('、');
        panel.appendChild(content);
        panel.appendChild(projectButton(metadataOnly ? '采用分类建议' : '采用本次候选', () => {
            if (parsedQuestionsData[index] !== question) return;
            if (typeof blockImportResetWhileSaving === 'function' && blockImportResetWhileSaving()) return;
            const next = questionsSnapshot();
            const onlyMetadata = candidate.metadata_only === true;
            const adopted = {...clone(onlyMetadata ? next[index] : candidate), document_import_selected: question.document_import_selected,
                document_import_fields_saved: true};
            adopted.knowledge_points = mergeKnowledgePointSuggestions(next[index].knowledge_points, candidate.knowledge_points);
            if (candidate.knowledge_point_warnings) adopted.knowledge_point_warnings = clone(candidate.knowledge_point_warnings);
            if (onlyMetadata) {
                metadataFields.forEach(field => {if (candidate[field] !== undefined) adopted[field] = clone(candidate[field]);});
                if (candidate.classification_review) {
                    adopted.classification_review = clone(candidate.classification_review);
                    if (question.classification_review?.original) adopted.classification_review.original = clone(question.classification_review.original);
                } else if (adopted.classification_review) {
                    adopted.classification_review = {...adopted.classification_review, required: false,
                        reason_codes: [], message: '分类已匹配。', confirmed_by: 'metadata'};
                }
                delete adopted.project_incoming_candidate;
            }
            if (question.saved_id) adopted.saved_id = question.saved_id;
            if (question.savedSnapshot) {adopted.savedSnapshot = clone(question.savedSnapshot);adopted.saved = false;}
            next[index] = adopted;state.questions = next;
            showQuestions(next);updateParsedSavedState(index);saveSoon();
        }));
        card.insertBefore(panel, card.firstChild);
    }

    function receiveLatestResult(data) {
        if (state && workingCopyRestore(data)) {
            state.latestResultId = data.latest_result_id || state.latestResultId || '';
            return false;
        }
        const incoming = Array.isArray(data.latest_result) ? data.latest_result : [];
        if (!incoming.length || !state || (data.latest_result_id && state.latestResultId === data.latest_result_id)) return false;
        const fingerprint = rows => JSON.stringify(rows.map(row => [itemId(row), correctionSnapshot(row),
            row.source_review || null, row.classification_review || null]));
        const baseline = Array.isArray(state.baselineQuestions) ? state.baselineQuestions : [];
        if (fingerprint(incoming) === fingerprint(baseline)) {
            state.latestResultId = data.latest_result_id || '';return false;
        }
        state.questions = parsedQuestionsData.length ? mergeCompletion(incoming) : clone(incoming);
        state.baselineQuestions = clone(incoming);state.latestResultId = data.latest_result_id || '';
        state.acceptedDiagnostics = clone(data.diagnostics || data.task?.diagnostics || state.acceptedDiagnostics || null);
        const previousRestoring = restoring;restoring = true;showQuestions(state.questions);restoring = previousRestoring;
        state.projectLocalDirty = true;editSerial += 1;
        if (state.projectSaveStatus !== 'conflict') state.projectSaveStatus = 'local';
        scheduleServerSave();return true;
    }

    function completed(task, questions) {
        const runtimeOnly = validId(state?.projectId) && !questions.length;
        if (workingCopyRestore(task)) {
            update({phase: task.status, taskId: task.task_id || state?.taskId,
                canResume: task.can_resume === true, diagnostics: task.diagnostics || null,
                error: task.error || '', sourceAvailable: task.source_available === true,
                projectId: task.project_id || state?.projectId, projectRevision: task.project_revision,
                projectRevisionReceipt: task.project_revision_receipt,
                expiresAt: task.expires_at, partial: task.partial === true});
            restoreEvidenceFlags(questions);
            state.questions = questionsSnapshot();
            persistOnly();
            return;
        }
        if (state && !runtimeOnly) state.acceptedDiagnostics = clone(task.diagnostics || null);
        update({ phase: task.status, taskId: task.task_id || state?.taskId,
            canResume: task.can_resume === true, diagnostics: task.diagnostics || null,
            baselineQuestions: clone(task.data || questions), error: task.error || '', sourceAvailable: task.source_available === true,
            projectId: task.project_id || state?.projectId, projectRevision: task.project_revision,
            projectRevisionReceipt: task.project_revision_receipt, expiresAt: task.expires_at, partial: task.partial === true });
        restoreEvidenceFlags(questions);
        if (!runtimeOnly) saveSoon();
        flush();
    }

    function projectRequest(path, options = {}) {
        if (pdfCloudOnly()) return Promise.reject(new Error('PDF 当前使用云端拆题，不再打开或保存本地工程。'));
        const match = /^\/([A-Za-z0-9_-]{1,160})(?:\/|$)/.exec(path);
        const id = match?.[1], deleting = options.method === 'DELETE';
        if (id && deletingProjects.has(id) && !deleting) return Promise.reject(new Error('工程正在删除，请稍后重试。'));
        const request = fetchDocumentImportJson('/api/document-projects' + path, {
            ...options, headers: { 'X-Local-Token': localStorage.getItem('local_token') || '',
                ...(options.body ? {'Content-Type': 'application/json'} : {}), ...(options.headers || {}) }
        });
        if (!id || deleting) return request;
        if (!pendingProjectRequests.has(id)) pendingProjectRequests.set(id, new Set());
        const pending = pendingProjectRequests.get(id);pending.add(request);
        const settled = () => {pending.delete(request);if (!pending.size) pendingProjectRequests.delete(id);};
        request.then(settled, settled);
        return request;
    }

    function draftSnapshot() {
        if (state && !Object.prototype.hasOwnProperty.call(state, 'acceptedDiagnostics')) state.acceptedDiagnostics = clone(state.diagnostics || null);
        const draft = clone(state || {});
        draft.diagnostics = clone(state?.acceptedDiagnostics || null);
        ['projectId', 'projectRevision', 'projectRevisionReceipt', 'projectSaveStatus', 'projectError', 'projectLocalDirty',
            'lastUsedAt', 'expiresAt', 'totalBytes', 'phase', 'log', 'updatedAt', 'taskId', 'clientRequestId',
            'documentType', 'canResume', 'error', 'sourceAvailable', 'partial', 'acceptedDiagnostics', 'localDraftPreviewTaskId',
            'projectSourcePageCount'].forEach(key => delete draft[key]);
        return draft;
    }

    function scheduleServerSave() {
        if (useTransientPdf()) return;
        if (!validId(state?.projectId) || deletingProjects.has(state.projectId)
                || !state.projectLocalDirty || state.projectSaveStatus === 'conflict') return;
        if (serverSaveTimer) clearTimeout(serverSaveTimer);
        serverSaveTimer = setTimeout(() => { serverSaveTimer = null; saveServerNow(); }, 450);
    }

    async function saveServerNow() {
        if (useTransientPdf()) {flush();return true;}
        if (!validId(state?.projectId)) return true;
        if (deletingProjects.has(state.projectId)) return false;
        if (saveTimer) flush();
        if (serverSaveTimer) clearTimeout(serverSaveTimer);
        serverSaveTimer = null;
        if (state.projectSaveStatus === 'conflict') return false;
        if (serverSaveInFlight) { serverSaveAgain = true; await serverSaveInFlight; return !state?.projectLocalDirty; }
        const owner = state, epoch = sessionEpoch, id = state.projectId, serial = editSerial;
        const expectedRevision = Number(owner.projectRevision || 0);
        const draft = draftSnapshot(), snapshot = JSON.stringify(draft);
        if (!owner.projectLocalDirty) return true;
        if (snapshot === lastServerDraft) {
            owner.projectLocalDirty = false;owner.projectSaveStatus = 'saved';persistOnly();return true;
        }
        owner.projectSaveStatus = 'saving';owner.projectError = '';renderRecovery();
        serverSaveProjectId = id;
        serverSaveInFlight = (async () => {
            try {
                const response = await projectRequest('/' + encodeURIComponent(id) + '/draft', {
                    method: 'PUT', body: JSON.stringify({expected_revision: expectedRevision, draft})
                });
                if (state !== owner || sessionEpoch !== epoch) return false;
                owner.projectRevision = response.revision;
                delete owner.projectRevisionReceipt;
                owner.lastUsedAt = response.last_used_at;owner.expiresAt = response.expires_at;
                lastServerDraft = snapshot;
                owner.projectLocalDirty = editSerial !== serial || JSON.stringify(draftSnapshot()) !== snapshot;
                owner.projectSaveStatus = owner.projectLocalDirty ? 'local' : 'saved';
                const listed = projectList.find(project => project.project_id === id);
                if (listed) {
                    // Retain metadata only; a saved 19-card draft must not
                    // leave the list showing its earlier three-card result.
                    listed.question_count = Number.isSafeInteger(response.question_count)
                        ? response.question_count : draft.questions.length;
                    ['total_bytes', 'last_used_at', 'last_used', 'expires_at', 'revision', 'status'].forEach(key => {
                        if (response[key] !== undefined) listed[key] = response[key];
                    });
                    renderProjects();
                }
                persistOnly();return true;
            } catch (error) {
                if (state !== owner || sessionEpoch !== epoch) return false;
                if (error.httpStatus === 409 && expectedRevision === 0 && owner.projectRevision === 1
                        && owner.projectRevisionReceipt?.base_revision === 0
                        && owner.projectRevisionReceipt?.revision === 1
                        && owner.projectRevisionReceipt?.project_id === id
                        && owner.projectRevisionReceipt?.task_id === owner.taskId) {
                    // The verified receipt arrived while this stale PUT was
                    // already in flight. Retry once using that receipt, never
                    // using an arbitrary revision reported by a 409 response.
                    owner.projectSaveStatus = 'local';owner.projectError = '';
                    serverSaveAgain = true;persistOnly();return false;
                }
                owner.projectSaveStatus = error.httpStatus === 409 ? 'conflict' : 'local';
                owner.projectError = error.httpStatus === 409
                    ? '服务器工程稿已更新。本机修改已保留，尚未覆盖服务器稿。'
                    : '未保存到本地服务：' + error.message + '。本机备份仍保留。';
                persistOnly();return false;
            } finally {
                if (state === owner && sessionEpoch === epoch) renderRecovery();
            }
        })();
        const result = await serverSaveInFlight;
        serverSaveInFlight = null;serverSaveProjectId = null;
        if (serverSaveAgain) { serverSaveAgain = false; scheduleServerSave(); }
        return result;
    }

    function persistOnly() {
        if (!state || useTransientPdf()) return;
        try {
            const serialized = JSON.stringify(state);
            localStorage.setItem(STORAGE_KEY, serialized);
            if (validId(state.projectId)) localStorage.setItem(STORAGE_KEY + ':' + state.projectId, serialized);
        } catch (error) { /* The existing storage warning is raised by flush. */ }
    }

    function expiryText(project) {
        const expires = Number(project.expires_at || project.expiresAt || 0) * 1000;
        if (!expires) return '最后使用后保留 7 天';
        const days = Math.max(0, Math.ceil((expires - Date.now()) / 86400000));
        return '剩余 ' + days + ' 天 · ' + new Date(expires).toLocaleString('zh-CN',
            {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}) + ' 到期';
    }

    function bytesText(bytes) {
        const count = Math.max(0, Number(bytes || 0));
        return count >= 1048576 ? (count / 1048576).toFixed(1) + ' MB' : Math.ceil(count / 1024) + ' KB';
    }

    function projectButton(text, callback, className = '') {
        const button = document.createElement('button');button.type = 'button';
        button.className = 'glass-btn rounded-lg px-3 py-2 text-xs font-semibold ' + className;
        button.textContent = text;button.addEventListener('click', callback);return button;
    }

    function renderProjects(message = '') {
        if (pdfCloudOnly()) {hidePdfProjectPanels();return;}
        const list = element('documentProjectsList');
        if (!list) return;
        list.replaceChildren();
        const explanation = document.createElement('p');
        explanation.className = 'text-[11px] text-slate-500';
        explanation.textContent = message || (projectListLoading ? '正在读取工程列表…'
            : projectList.length ? '只在打开工程时载入稿件；最后使用后保留 7 天。' : '暂无本地 PDF 工程。上传 PDF 后会自动保存工程。');
        list.appendChild(explanation);
        projectList.forEach(project => {
            if (!validId(project.project_id) || deletedProjects.has(project.project_id)) return;
            const deleting = deletingProjects.has(project.project_id);
            const row = document.createElement('div');row.dataset.projectId = project.project_id;
            if (deleting) row.setAttribute('aria-busy', 'true');
            row.className = 'rounded-lg border border-slate-200 p-3 space-y-2';
            const title = document.createElement('p');title.className = 'font-semibold break-words';
            title.textContent = String(project.title || project.filename || '未命名 PDF 工程');row.appendChild(title);
            const meta = document.createElement('p');meta.className = 'text-[11px] text-slate-500';
            const statusLabels = {completed: '可继续校对', partial: '拆解未完成', error: '拆解未完成',
                interrupted: '解析已暂停', cancelled: '已取消', created: '待继续解析', corrupt: '工程需检查'};
            const statusLabel = Number(project.question_count) === 0 && ['completed', 'partial'].includes(project.status)
                ? '题卡尚未整理' : statusLabels[project.status] || '解析处理中';
            meta.textContent = statusLabel + ' · ' + expiryText(project) +
                ' · ' + bytesText(project.total_bytes) + ' · ' + Number(project.question_count || 0) + ' 题';row.appendChild(meta);
            const actions = document.createElement('div');actions.className = 'flex flex-wrap gap-2';
            const openButton = projectButton('继续校对', () => openProject(project.project_id));openButton.disabled = deleting;
            const deleteButton = projectButton(deleting ? '正在删除…' : '删除', () => deleteProject(project.project_id), 'text-red-600');
            deleteButton.disabled = deleting;actions.appendChild(openButton);actions.appendChild(deleteButton);
            row.appendChild(actions);list.appendChild(row);
        });
    }

    async function refreshProjects() {
        if (pdfCloudOnly()) {hidePdfProjectPanels();return false;}
        if (projectListLoading) return;
        projectListLoading = true;renderProjects();
        try {
            const data = await projectRequest('');
            projectList = Array.isArray(data.projects) ? data.projects.filter(project => !deletedProjects.has(project.project_id)) : [];
            projectListLoading = false;
            renderProjects();
        } catch (error) { projectListLoading = false;renderProjects('工程列表暂不可用：' + error.message); }
        finally { projectListLoading = false; }
    }

    function backupUnsynced() {
        if (transientPdf()) return;
        if (!validId(state?.projectId) || !state.projectLocalDirty) return;
        try { localStorage.setItem(STORAGE_KEY + ':unsynced:' + state.projectId, JSON.stringify(state)); } catch (error) { /* keep current draft */ }
    }

    async function openProject(id, options = {}) {
        if (pdfCloudOnly()) return false;
        if (!validId(id) || busy || deletingProjects.has(id) || deletedProjects.has(id)) return false;
        const openingEpoch = sessionEpoch;let adoptedState = null;busy = true;renderRecovery();
        try {
            if (!options.skipCurrentSave) { flush();await saveServerNow(); }
            if (sessionEpoch !== openingEpoch || deletingProjects.has(id) || deletedProjects.has(id)) return false;
            backupUnsynced();
            // Opening returns the authoritative draft and renews its lease in
            // one request; avoid loading a large project's body twice.
            const data = await projectRequest('/' + encodeURIComponent(id) + '/open', {method: 'POST'});
            const opened = data;
            if (sessionEpoch !== openingEpoch || deletingProjects.has(id) || deletedProjects.has(id)) return false;
            // Capture keystrokes made while the project request was pending.
            // The old project's backup must not depend on a timer that would
            // otherwise run after a different project has been displayed.
            flush();backupUnsynced();
            if (serverSaveTimer) clearTimeout(serverSaveTimer);
            serverSaveTimer = null;
            let local = null;
            if (!options.serverOnly) {
                try { local = JSON.parse(localStorage.getItem(STORAGE_KEY + ':' + id) || 'null'); } catch (error) { /* use server draft */ }
            }
            const useLocal = local?.projectLocalDirty === true;
            const draft = useLocal ? local : (data.draft || {});
            sessionEpoch += 1;restored = true;restoring = true;lastServerDraft = '';
            state = {version: 1, questions: [], baselineQuestions: [], ...clone(draft), projectId: id,
                projectRevision: useLocal ? local.projectRevision : data.revision,
                expiresAt: opened.expires_at || data.expires_at, lastUsedAt: opened.last_used_at || data.last_used_at,
                totalBytes: data.total_bytes, documentType: data.document_type || 'pdf',
                pageImages: Array.isArray(draft.pageImages) && draft.pageImages.length ? draft.pageImages : (Array.isArray(data.page_images) ? data.page_images : []),
                pageNumbers: Array.isArray(draft.pageNumbers) && draft.pageNumbers.length ? draft.pageNumbers : (Array.isArray(data.page_numbers) ? data.page_numbers : []),
                taskId: data.task_id || data.task?.task_id || draft.taskId,
                phase: data.task?.status || data.status || draft.phase || 'completed', canResume: data.can_resume === true,
                sourceAvailable: true, error: data.task?.error || draft.error || '',
                diagnostics: data.diagnostics || data.task?.diagnostics || draft.diagnostics || null,
                acceptedDiagnostics: clone(draft.diagnostics || data.diagnostics || data.task?.diagnostics || null),
                partial: data.partial === true || data.task?.partial === true, projectLocalDirty: useLocal,
                projectSourcePageCount: Array.isArray(data.source_pages) ? data.source_pages.length : 0,
                projectSaveStatus: useLocal ? (Number(local.projectRevision) !== Number(data.revision) ? 'conflict' : 'local') : 'saved'};
            if (useLocal && local.taskId === state.taskId) acknowledgeInitialRevision({projectId: id,
                taskId: state.taskId, projectRevision: data.revision, projectRevisionReceipt: data.project_revision_receipt});
            adoptedState = state;
            if (!state.paperTitle) state.paperTitle = data.title || '';
            if (!state.questions.length && !data.draft && Array.isArray(data.task?.data)) state.questions = clone(data.task.data);
            if (!Array.isArray(state.page_edits) || !state.page_edits.length) {
                state.page_edits = (Array.isArray(data.source_pages) ? data.source_pages : [])
                    .map(page => ({page_number: page.page_number, markdown: String(page.markdown || '')}));
            }
            window.currentPdfTaskId = validId(state.taskId) ? state.taskId : null;
            window.currentDocumentClientRequestId = null;
            window.currentPdfFile = null;window.currentDocxFile = null;
            if (element('texFileInput')) element('texFileInput').value = '';
            if (element('importLatexContent')) {element('importLatexContent').value = '';element('importLatexContent').disabled = false;}
            if (typeof updateImportSourceView === 'function') updateImportSourceView('empty');
            if (element('texFileName')) element('texFileName').textContent = '选择新试卷文件；当前工程可直接下载原 PDF';
            window.pdfPageImages = Array.isArray(state.pageImages) ? state.pageImages : [];
            window.pdfPageNumbers = Array.isArray(state.pageNumbers) ? state.pageNumbers : [];
            if (element('importPaperTitle')) element('importPaperTitle').value = state.paperTitle;
            if (typeof stopCurrentDocumentPoll === 'function') stopCurrentDocumentPoll();
            showQuestions(state.questions);receiveLatestResult(data);restoring = false;
            if (!state.projectLocalDirty) lastServerDraft = JSON.stringify(draftSnapshot());
            persistOnly();renderPageEditor();renderRecovery();observeProjectTask();
            if (typeof selectWorkspace === 'function') selectWorkspace('import', '导入中心');
            if (useLocal && state.projectSaveStatus !== 'conflict') scheduleServerSave();
            return true;
        } catch (error) { showToast('打开工程失败：' + error.message + '。原有本机稿仍保留。', 'warning');return false; }
        finally {
            if (sessionEpoch === openingEpoch || state === adoptedState) {restoring = false;busy = false;renderRecovery();}
        }
    }

    async function deleteProject(id) {
        if (pdfCloudOnly()) return false;
        if (!validId(id) || deletingProjects.has(id) || deletedProjects.has(id)
                || !confirm('删除此本地 PDF 工程及其原文件、识别稿和校对稿？已入库题目不受影响。')) return;
        deletingProjects.add(id);
        if (state?.projectId === id) {
            // Retain the current keystrokes if deletion fails, but do not
            // race the deletion with a newly debounced draft write.
            flush();backupUnsynced();
            if (serverSaveTimer) clearTimeout(serverSaveTimer);
            serverSaveTimer = null;
        }
        renderProjects();renderRecovery();
        try {
            const pending = [...(pendingProjectRequests.get(id) || [])];
            if (serverSaveProjectId === id && serverSaveInFlight) pending.push(serverSaveInFlight);
            await Promise.allSettled(pending);
            await projectRequest('/' + encodeURIComponent(id), {method: 'DELETE'});
            deletedProjects.add(id);
            projectList = projectList.filter(project => project.project_id !== id);renderProjects();
            if (state?.projectId === id) {
                if (typeof stopCurrentDocumentPoll === 'function') stopCurrentDocumentPoll();
                clear({discardDeletedProject: true});showQuestions([]);element('parsedQuestionsWrapper')?.classList.add('hidden');renderPageEditor();
            }
            try {
                localStorage.removeItem(STORAGE_KEY + ':' + id);
                localStorage.removeItem(STORAGE_KEY + ':unsynced:' + id);
            } catch (error) { /* A committed server deletion remains successful if browser storage is blocked. */ }
            refreshProjects();
        } catch (error) { showToast('删除工程失败：' + error.message, 'warning'); }
        finally {
            deletingProjects.delete(id);renderProjects();renderRecovery();
            if (!deletedProjects.has(id) && state?.projectId === id) scheduleServerSave();
        }
    }

    async function forkProject() {
        if (pdfCloudOnly()) return false;
        if (!validId(state?.projectId) || busy || deletingProjects.has(state.projectId)) return;
        const owner = state, epoch = sessionEpoch;
        flush();busy = true;renderRecovery();
        try {
            const data = await projectRequest('/' + encodeURIComponent(state.projectId) + '/fork',
                {method: 'POST', body: JSON.stringify({draft: draftSnapshot()})});
            if (state !== owner || sessionEpoch !== epoch) return;
            backupUnsynced();busy = false;await openProject(data.project_id, {skipCurrentSave: true});await refreshProjects();
        } catch (error) { showToast('另存工程失败：' + error.message + '。本机稿仍保留。', 'warning'); }
        finally { if (state === owner) {busy = false;renderRecovery();} }
    }

    async function loadProjectSourcePages() {
        if (pdfCloudOnly()) return;
        if (!validId(state?.projectId) || sourcePagesLoading) return;
        const owner = state, epoch = sessionEpoch;sourcePagesLoading = true;
        try {
            const data = await projectRequest('/' + encodeURIComponent(owner.projectId));
            if (state !== owner || sessionEpoch !== epoch || owner.page_edits?.length) return;
            owner.page_edits = (Array.isArray(data.source_pages) ? data.source_pages : [])
                .map(page => ({page_number: page.page_number, markdown: String(page.markdown || '')}));
            owner.projectSourcePageCount = owner.page_edits.length;
            persistOnly();renderPageEditor();renderRecovery();observeProjectTask();
        } catch (error) { /* Reconnect and project list retain an explicit retry path. */ }
        finally { sourcePagesLoading = false; }
    }

    function renderPageEditor() {
        let panel = element('documentProjectPageEditor');
        if (pdfCloudOnly()) {if (panel) panel.hidden = true;return;}
        const pages = Array.isArray(state?.page_edits) ? state.page_edits : [];
        if (!pages.length) { if (panel) panel.hidden = true;return; }
        if (!panel) {
            const loading = element('importLoadingState');if (!loading?.parentElement) return;
            panel = document.createElement('details');panel.id = 'documentProjectPageEditor';
            panel.className = 'rounded-xl border border-slate-200 bg-white p-3 space-y-3 text-xs shrink-0';
            loading.parentElement.insertBefore(panel, loading);
        }
        panel.hidden = false;panel.replaceChildren();
        const summary = document.createElement('summary');summary.className = 'cursor-pointer font-semibold';
        summary.textContent = '逐页识别工作稿（' + pages.length + ' 页，可校对）';panel.appendChild(summary);
        const note = document.createElement('p');note.className = 'text-[11px] text-slate-500';
        note.textContent = localDraftMode() ? '逐页修改随工程自动保存。可从保存的识图稿在本地重新整理题卡，再校对入库。'
            : '逐页修改随工程自动保存。重新分析使用启动时已保存的工作稿，不重新识别 PDF。';panel.appendChild(note);
        pages.forEach((page, index) => {
            const label = document.createElement('label');label.className = 'block space-y-1';
            const title = document.createElement('span');title.textContent = '第 ' + Number(page.page_number) + ' 页';label.appendChild(title);
            const input = document.createElement('textarea');input.className = 'document-project-page-edit glass-input w-full h-44 p-3 rounded-lg font-mono text-xs';
            input.value = String(page.markdown || '');input.dataset.pageNumber = String(page.page_number);
            input.addEventListener('input', () => { if (state?.page_edits?.[index] === page) { page.markdown = input.value;saveSoon(); } });
            label.appendChild(input);panel.appendChild(label);
        });
        panel.appendChild(projectButton(localDraftMode() ? '从逐页稿重新整理题卡' : '从工作稿重新分析', analyzeProject));
    }

    async function prepareQuestionImport(question, index) {
        if (useTransientPdf()) return null;
        if (!validId(state?.projectId)) return null;
        if (deletingProjects.has(state.projectId)) throw new Error('工程正在删除，本次没有导入。');
        const owner = state, epoch = sessionEpoch;
        if (parsedQuestionsData[index] !== question) throw new Error('题卡已变化，请重新导入。');
        if (!validId(question.project_card_id)) {
            question.project_card_id = window.crypto?.randomUUID ? window.crypto.randomUUID()
                : 'card_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2);
        }
        saveSoon();flush();
        for (let attempt = 0; attempt < 3 && owner.projectLocalDirty; attempt++) {
            flush();
            if (!await saveServerNow()) break;
        }
        if (state !== owner || sessionEpoch !== epoch || parsedQuestionsData[index] !== question) {
            throw new Error('工程或题卡已切换，本次没有导入。');
        }
        if (deletingProjects.has(owner.projectId)) throw new Error('工程正在删除，本次没有导入。');
        if (owner.projectLocalDirty || owner.projectSaveStatus === 'conflict') {
            throw new Error('工程校对稿尚未保存到本地服务，请先处理工程保存提示。');
        }
        return {project_id: owner.projectId, card_id: question.project_card_id, draft_revision: owner.projectRevision};
    }

    async function analyzeProject() {
        if (pdfCloudOnly()) return false;
        if (!validId(state?.projectId) || busy || deletingProjects.has(state.projectId) || !state.page_edits?.length) return;
        const owner = state, epoch = sessionEpoch;
        flush();if (!await saveServerNow() || state.projectLocalDirty) {
            showToast('工作稿尚未保存到本地服务，请先处理保存提示。', 'warning');return;
        }
        if (state !== owner || sessionEpoch !== epoch) return;
        busy = true;renderRecovery();
        try {
            const data = await projectRequest('/' + encodeURIComponent(state.projectId) + '/analyze', {method: 'POST'});
            if (state !== owner || sessionEpoch !== epoch) return;
            const taskId = data.task_id || data.task?.task_id;if (!validId(taskId)) throw new Error('后台未返回分析任务。');
            restored = true;const generation = beginDocumentImportTask({skipCancellation: true});
            window.currentPdfTaskId = taskId;update({taskId, phase: 'running', error: ''});
            element('importLoadingState')?.classList.remove('hidden');pollPdfTaskStatus(taskId, generation);
        } catch (error) { showToast('工作稿分析未启动：' + error.message, 'warning'); }
        finally { if (state === owner) {busy = false;renderRecovery();} }
    }

    function restoreEvidenceFlags(questions) {
        questions.forEach((question, index) => {
            if (question.document_import_source_edited) {
                parsedSourceReviewSnapshots.set(question, null);
                parsedSourceVisionVerifications.set(question, null);
                updateParsedSourceReviewState(index);
            }
        });
    }

    function addOption(select, value, label) {
        if (!select) return;
        const options = Array.from(select.options || select.children || []);
        const existing = options.find(option => option.value === value);
        if (existing) {
            if (label !== undefined) existing.textContent = label || value || '-- 未选择 --';
            return;
        }
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label || value || '-- 未选择 --';
        select.appendChild(option);
    }

    function restoreCardFields(card, question) {
        if (!card || !question.document_import_fields_saved) return;
        restoredCards.add(card);
        Object.entries(fields).forEach(([name, selector]) => {
            if (!Object.prototype.hasOwnProperty.call(question, name)) return;
            const input = card.querySelector(`.card-${selector}`);
            if (!input) return;
            const value = String(question[name] || '');
            if (typeof input.tagName === 'string' && input.tagName.toLowerCase() === 'select') {
                addOption(input, value);
                input.disabled = false;
            }
            input.value = value;
        });
    }

    function refreshCatalogs() {
        const tree = window.categoryTree || (typeof categoryTree !== 'undefined' ? categoryTree : {});
        const metadata = window.systemMetadata || {};
        parsedQuestionsData.forEach((question, index) => {
            const card = element(`parsed-card-${index}`);
            if (!card || !restoredCards.has(card)) return;
            const values = Object.fromEntries(Object.entries(fields).map(([name, selector]) =>
                [name, card.querySelector(`.card-${selector}`)?.value || '']));
            const compulsory = card.querySelector('.card-compulsory');
            const chapter = card.querySelector('.card-chapter');
            const knowledge = card.querySelector('.card-knowledge');
            Object.keys(tree).forEach(value => addOption(compulsory, value));
            const chapters = tree[values.category_compulsory] || {};
            Object.keys(chapters).forEach(value => addOption(chapter, value));
            (Array.isArray(chapters[values.category_chapter]) ? chapters[values.category_chapter] : [])
                .forEach(value => addOption(knowledge, value));
            (metadata.question_types || []).forEach(item => addOption(card.querySelector('.card-qtype'), item.value, item.label));
            (metadata.difficulties || []).forEach(item => addOption(card.querySelector('.card-difficulty'), item.value, item.label));
            // Appending a first option can select it implicitly; preserve even an
            // explicit empty choice and every edit made while the catalog loaded.
            Object.entries(fields).forEach(([name, selector]) => {
                const input = card.querySelector(`.card-${selector}`);
                if (input && typeof input.tagName === 'string' && input.tagName.toLowerCase() === 'select') {
                    input.value = values[name];
                }
            });
        });
    }

    function showQuestions(questions) {
        replaceParsedQuestions(questions);
        renderParsedQuestionsList(parsedQuestionsData);
        parsedQuestionsData.forEach((question, index) => restoreCardFields(element(`parsed-card-${index}`), question));
        refreshCatalogs();
        restoreEvidenceFlags(parsedQuestionsData);
        parsedQuestionsData.forEach((question, index) => {
            const checkbox = element(`parsed-card-${index}`)?.querySelector('.card-select-checkbox');
            if (checkbox && !checkbox.disabled && typeof question.document_import_selected === 'boolean') {
                checkbox.checked = question.document_import_selected;
            }
        });
        renderSourceIntegrityReport(state?.diagnostics || null);
        updateSelectedCount();
        element('importPlaceholder')?.classList.add('hidden');
        element('parsedQuestionsWrapper')?.classList.toggle('hidden', validId(state?.projectId) && !questions.length);
    }

    function projectTaskIsActive() {
        return validId(state?.projectId) && validId(state?.taskId) && state.canResume !== true
            && !['completed', 'partial', 'error', 'interrupted', 'cancelled', 'created', 'missing', 'disconnected'].includes(state.phase);
    }

    function observeProjectTask() {
        if (!projectTaskIsActive()) return;
        const generation = beginDocumentImportTask({skipCancellation: true});
        window.currentPdfTaskId = state.taskId;
        pollPdfTaskStatus(state.taskId, generation);
    }

    async function reconnectProject() {
        if (pdfCloudOnly()) return false;
        if (!validId(state?.projectId) || busy || deletingProjects.has(state.projectId)) return;
        const owner = state, epoch = sessionEpoch;busy = true;renderRecovery();
        try {
            const data = await projectRequest('/' + encodeURIComponent(owner.projectId) + '/open', {method: 'POST'});
            const opened = data;
            if (state !== owner || sessionEpoch !== epoch) return;
            acknowledgeInitialRevision({projectId: owner.projectId, taskId: data.task_id || data.task?.task_id,
                projectRevision: data.revision, projectRevisionReceipt: data.project_revision_receipt});
            owner.canResume = data.can_resume === true;owner.expiresAt = opened.expires_at || data.expires_at;
            owner.phase = data.task?.status || data.status || owner.phase;
            owner.taskId = data.task_id || data.task?.task_id || owner.taskId;owner.error = data.task?.error || '';
            owner.diagnostics = data.diagnostics || data.task?.diagnostics || owner.diagnostics;
            owner.partial = data.partial === true || data.task?.partial === true;
            owner.totalBytes = data.total_bytes;
            if (Number(data.revision) !== Number(owner.projectRevision)) {
                owner.projectSaveStatus = 'conflict';owner.projectError = '服务器稿已更新。本机校对稿保留，请选择重载或另存副本。';
            } else if (owner.projectLocalDirty) scheduleServerSave();
            else {owner.projectSaveStatus = 'saved';owner.projectError = '';}
            if (!owner.page_edits?.length && Array.isArray(data.source_pages)) {
                owner.page_edits = data.source_pages.map(page => ({page_number: page.page_number, markdown: String(page.markdown || '')}));
            }
            receiveLatestResult(data);persistOnly();renderPageEditor();observeProjectTask();
        } catch (error) {
            if (state !== owner || sessionEpoch !== epoch) return;
            owner.projectSaveStatus = 'local';owner.projectError = '工程服务暂不可用：' + error.message + '。本机稿仍保留。';persistOnly();
        } finally { if (state === owner) {busy = false;renderRecovery();} }
    }

    async function reconnect() {
        if (validId(state?.projectId)) return reconnectProject();
        if (!state || busy) return;
        busy = true;
        renderRecovery();
        try {
            const taskId = validId(state.taskId) ? state.taskId : null;
            const requestId = validId(state.clientRequestId) ? state.clientRequestId : null;
            if (!taskId && !requestId) throw new Error('此会话仅保留校对内容，请重新选择原文件开始新的解析。');
            const task = await fetchDocumentImportJson(taskId
                ? `/api/tasks/${encodeURIComponent(taskId)}/status`
                : `/api/tasks/by-request/${encodeURIComponent(requestId)}/status`);
            const resolvedId = task.task_id || taskId;
            if (!validId(resolvedId) && task.status !== 'cancelled') throw new Error('后台任务尚未登记，请稍后重新连接。');
            window.currentPdfTaskId = resolvedId;
            window.currentDocumentClientRequestId = requestId;
            update({ taskId: resolvedId, phase: task.status, documentType: task.document_type,
                canResume: task.can_resume === true, error: task.error || '', diagnostics: task.diagnostics || state.diagnostics,
                sourceAvailable: task.source_available === true });
            if (Array.isArray(task.page_images) && task.page_images.length) {
                window.pdfPageImages = task.page_images;
                window.pdfPageNumbers = Array.isArray(task.page_numbers) ? task.page_numbers : [];
            }
            if (['completed', 'partial', 'error', 'interrupted', 'cancelled'].includes(task.status)) {
                if (Array.isArray(task.data) && task.data.length) {
                    const questions = parsedQuestionsData.length ? mergeCompletion(task.data) : task.data;
                    state.baselineQuestions = clone(task.data);
                    showQuestions(questions);
                }
                if (parsedQuestionsData.length && resolvedId) startDocumentResultRetention(resolvedId);
                element('importLoadingState')?.classList.add('hidden');
            } else {
                const generation = beginDocumentImportTask({ skipCancellation: true });
                window.currentPdfTaskId = resolvedId;
                window.currentDocumentClientRequestId = requestId;
                element('importPlaceholder')?.classList.add('hidden');
                element('importLoadingState')?.classList.remove('hidden');
                if (element('runParseBtn')) element('runParseBtn').disabled = true;
                pollPdfTaskStatus(resolvedId, generation);
            }
            flush();
        } catch (error) {
            update({ phase: error.httpStatus === 404 || error.httpStatus === 410 ? 'missing' : 'disconnected', error: error.message });
            showToast(`任务连接尚未恢复：${error.message}。当前校对内容仍保留。`, 'warning');
        } finally {
            busy = false;
            renderRecovery();
        }
    }

    async function resume() {
        if (transientPdf()) return;
        if (!state?.canResume || (!validId(state.taskId) && !validId(state.projectId)) || busy
                || deletingProjects.has(state.projectId)) return;
        const owner = state, epoch = sessionEpoch;
        flush();
        if (validId(state.projectId) && (!await saveServerNow() || state.projectLocalDirty)) return;
        if (state !== owner || sessionEpoch !== epoch) return;
        busy = true;
        renderRecovery();
        try {
            let taskId = state.taskId;
            const response = validId(state.projectId) ? await projectRequest('/' + encodeURIComponent(state.projectId) + '/resume', {method: 'POST'})
                : await fetchDocumentImportJson(`/api/tasks/${encodeURIComponent(taskId)}/resume`, {
                method: 'POST', headers: { 'X-Local-Token': localStorage.getItem('local_token') || '' }
            });
            if (state !== owner || sessionEpoch !== epoch) return;
            if (response.status !== 'success') throw new Error(response.message || '后台未接受继续请求。');
            taskId = response.task_id || response.task?.task_id || taskId;
            restored = true;
            const generation = beginDocumentImportTask({ skipCancellation: true });
            window.currentPdfTaskId = taskId;
            window.currentDocumentClientRequestId = state.clientRequestId;
            update({ phase: 'running', canResume: false, error: '' });
            element('importLoadingState')?.classList.remove('hidden');
            if (element('runParseBtn')) element('runParseBtn').disabled = true;
            pollPdfTaskStatus(taskId, generation);
        } catch (error) {
            if (state !== owner || sessionEpoch !== epoch) return;
            update({ error: error.message });
            showToast(`继续解析失败：${error.message}。已有题卡仍保留。`, 'warning');
        } finally {
            if (state === owner) {busy = false;renderRecovery();}
        }
    }

    function renderRecovery() {
        let panel = element('documentImportRecoveryPanel');
        if (transientPdf()) {if (panel) panel.hidden = true;return;}
        const sourceResult = state?.sourceAvailable === true
            && ['completed', 'partial', 'error', 'interrupted', 'cancelled'].includes(state.phase);
        const show = state && (validId(state.projectId) || restored || sourceResult || ['error', 'interrupted', 'partial', 'missing', 'disconnected',
            'creation_uncertain', 'cancellation_pending'].includes(state.phase));
        if (!show) { if (panel) panel.hidden = true; return; }
        if (!panel) {
            const loading = element('importLoadingState');
            if (!loading?.parentElement) return;
            panel = document.createElement('div');
            panel.id = 'documentImportRecoveryPanel';
            panel.className = 'rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-xs text-slate-700 space-y-2 shrink-0';
            panel.setAttribute('role', 'status');
            loading.parentElement.insertBefore(panel, loading);
        }
        panel.hidden = false;
        const structureReview = typeof pdfStructureReviewState === 'function' ? pdfStructureReviewState(state.diagnostics, state) : {incomplete: false, lines: []};
        // Source checkpoints and teacher-edited card drafts have separate
        // receipts. A zero-card result has no draft PUT to wait for.
        const sourcePersisted = state.diagnostics?.pdf_local_draft?.persisted === true
            || Number(state.projectSourcePageCount) > 0;
        const sourcePageCount = Number(state.projectSourcePageCount) || new Set((state.page_edits || [])
            .map(page => page.page_number).filter(number => Number.isInteger(number) && number > 0)).size;
        const savedSourceText = '原文件和' + (sourcePageCount ? sourcePageCount + '页' : '') + '识图稿已保存；题卡尚未整理';
        const messagePhase = projectTaskIsActive()
            ? (['local_draft', 'ai_metadata'].includes(state.phase) ? state.phase : 'project_running') : state.phase;
        const renderKey = JSON.stringify([messagePhase, state.error, state.projectId, state.taskId, state.clientRequestId,
            state.projectSaveStatus, state.projectError, state.canResume, validId(state.projectId) ? true : state.sourceAvailable, restored, busy,
            expiryText(state), state.totalBytes, deletingProjects.has(state.projectId), !parsedQuestionsData.length && Boolean(state.page_edits?.length),
            structureReview.incomplete ? structureReview.lines : false, structureReview.summary || '', sourcePersisted, sourcePageCount]);
        if (panel.dataset.renderKey === renderKey) return;
        panel.dataset.renderKey = renderKey;
        panel.replaceChildren();
        const message = document.createElement('p');
        const descriptions = {
            completed: '已恢复上次拆卷的题卡与校对内容，可继续编辑和入库。',
            cancelled: '后台已确认取消；已保留的题卡仍可编辑和入库。',
            cancellation_pending: '取消尚待确认，可重新连接查看后台状态。',
            running: '已重新连接上次拆卷任务，正在读取进度。',
            project_running: '正在处理试卷，工程与原文件已保留。',
            local_draft: '首次识图稿已保存，正在本地整理题卡。',
            ai_metadata: '本地题稿已保留，正在匹配题型和教材分类，可继续校对。',
            missing: '暂时找不到后台任务；已保留校对内容，可重新连接。',
            disconnected: '与后台的连接已暂停；任务与校对内容已保留。',
            creating: '已保留上次上传请求，正在查询后台是否已创建任务。',
            creation_uncertain: '上传结果暂未确认，请重新连接查询，避免重复上传。'
        };
        message.textContent = structureReview.localDraft
            ? (structureReview.incomplete
                ? (parsedQuestionsData.length ? '首次识图稿已保存，已整理的题卡可继续校对入库；未归属原文可展开逐页校对。'
                    : (sourcePersisted ? savedSourceText + '，可展开逐页校对。' : '首次识图稿已保存，待逐页校对后在本地重新整理题卡。'))
                : (['local_draft', 'ai_metadata'].includes(messagePhase) ? descriptions[messagePhase]
                    : '首次识图稿已保存，本地题稿可直接校对入库。'))
            : structureReview.incomplete
            ? '拆题尚未完成，首次识别工作稿已保留。可展开逐页校对后重新分析。'
            : validId(state.projectId) && !parsedQuestionsData.length && state.page_edits?.length
                && ['error', 'partial', 'interrupted'].includes(state.phase)
            ? '拆题尚未完成，逐页识别工作稿已保留，可展开校对后重新分析。'
            : state.phase === 'completed' && !restored ? '拆卷结果已保留，可下载原文件对照校对。'
            : descriptions[messagePhase] || (['error', 'interrupted', 'partial'].includes(state.phase)
                ? '拆卷已暂停，已有题卡和校对内容已保留。' : '已恢复上次任务，正在读取处理进度。');
        panel.appendChild(message);
        if (structureReview.summary) {
            const summary = document.createElement('p');summary.className = 'document-project-parse-summary text-[11px] text-slate-500';
            summary.textContent = structureReview.summary;panel.appendChild(summary);
        }
        if (structureReview.incomplete) {
            const reasons = document.createElement('p');
            reasons.className = 'document-project-manual-reasons whitespace-pre-wrap text-amber-800';
            reasons.textContent = structureReview.lines.join('\n');panel.appendChild(reasons);
        }
        if (validId(state.projectId)) {
            const status = document.createElement('p');status.className = 'document-project-save-status text-[11px] whitespace-pre-wrap';
            const labels = {saving: '正在保存到本地服务…', saved: '已保存到本地服务', local: '本机备份已保留，尚未保存到本地服务', conflict: '存在保存冲突，本机修改已保留'};
            const editing = state.projectLocalDirty || ['saving', 'local', 'conflict'].includes(state.projectSaveStatus);
            const saveLabel = !editing && sourcePersisted && !parsedQuestionsData.length
                ? '已保存到本地服务（原文件与逐页识图稿）' : labels[state.projectSaveStatus] || '等待保存到本地服务';
            status.textContent = saveLabel + ' · ' + expiryText(state) +
                (state.totalBytes ? ' · ' + bytesText(state.totalBytes) : '') + (state.projectError ? '\n' + state.projectError : '');
            panel.appendChild(status);
        }
        if (state.error) {
            const detail = document.createElement('p');
            detail.className = 'text-[11px] text-slate-500 whitespace-pre-wrap';
            detail.textContent = String(state.error);
            panel.appendChild(detail);
        }
        const actions = document.createElement('div');
        actions.className = 'flex flex-wrap gap-2';
        const addButton = (text, callback) => {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'glass-btn px-3 py-1.5 rounded-lg font-semibold';
            button.textContent = text; button.disabled = busy || deletingProjects.has(state.projectId);
            button.addEventListener('click', callback); actions.appendChild(button);
        };
        if (state.projectId || state.taskId || state.clientRequestId) addButton(busy ? '正在连接…' : '重新连接', reconnect);
        if (validId(state.projectId) || (state.sourceAvailable && validId(state.taskId))) {
            const download = document.createElement('a');
            download.className = 'glass-btn px-3 py-1.5 rounded-lg font-semibold';
            download.textContent = '下载原文件';
            download.href = validId(state.projectId) ? '/api/document-projects/' + encodeURIComponent(state.projectId) + '/source'
                : `/api/tasks/${encodeURIComponent(state.taskId)}/source`;
            download.setAttribute('download', '');
            actions.appendChild(download);
        }
        if (state.canResume) addButton('继续未完成解析', resume);
        if (validId(state.projectId) && state.projectSaveStatus === 'conflict') {
            addButton('重载服务器稿（本机稿留备份）', () => openProject(state.projectId, {serverOnly: true}));
            addButton('另存为工程副本', forkProject);
        } else if (validId(state.projectId) && state.projectSaveStatus === 'local') {
            addButton('重试保存', saveServerNow);
        }
        if (projectTaskIsActive() || ['creating', 'creation_uncertain', 'disconnected', 'cancellation_pending', 'running'].includes(state.phase)) {
            addButton('请求取消后台任务', () => {
                window.currentPdfTaskId = state.taskId;
                window.currentDocumentClientRequestId = state.clientRequestId;
                cancelCurrentImportTask();
            });
        }
        panel.appendChild(actions);
        if (validId(state.projectId)) {
            const loading = element('importLoadingState');
            if (loading) loading.classList.toggle('hidden', !projectTaskIsActive());
            if (element('runParseBtn')) element('runParseBtn').disabled = projectTaskIsActive();
        }
    }

    function restore() {
        try {
            const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
            if (!saved) return;
            if (transientPdf(saved)) return;
            if (saved.version !== 1 || !Array.isArray(saved.questions)
                    || saved.questions.some(question => !question || typeof question !== 'object' || Array.isArray(question))) {
                throw new Error('导入会话格式不完整。');
            }
            state = saved; restored = true; restoring = true;
            window.currentPdfTaskId = validId(saved.taskId) ? saved.taskId : null;
            window.currentDocumentClientRequestId = validId(saved.clientRequestId) ? saved.clientRequestId : null;
            window.pdfPageImages = Array.isArray(saved.pageImages) ? saved.pageImages : [];
            window.pdfPageNumbers = Array.isArray(saved.pageNumbers) ? saved.pageNumbers : [];
            if (element('importPaperTitle')) element('importPaperTitle').value = String(saved.paperTitle || '');
            if (element('importLatexContent')) { element('importLatexContent').value = ''; element('importLatexContent').disabled = false; }
            if (saved.questions.length) {
                saved.questions.forEach(question => { question.document_import_fields_saved = true; });
                showQuestions(saved.questions);
            }
        } catch (error) {
            showToast('上次导入会话无法完整读取，原有本地草稿不受影响。', 'warning');
        } finally {
            restoring = false;
            renderRecovery();renderPageEditor();
        }
        if (state && (state.taskId || state.clientRequestId)) reconnect();
    }

    window.MathBankDocumentImportSession = { begin, update, completed, mergeCompletion, previewLocalDraft,
        saveSoon, flush, clear, prepareNewImport: clear, reconnect, resume, refreshCatalogs, isRestored: () => restored, isProject: () => validId(state?.projectId), getProjectId: () => state?.projectId || null,
        refreshProjects, openProject, deleteProject, saveServerNow, forkProject, analyzeProject, prepareQuestionImport, renderCandidate };
    document.addEventListener('input', event => {
        if (event.target.closest?.('#parsedCardsContainer, #importPaperTitle')) {
            if (validId(state?.projectId)) restored = true;
            saveSoon();
        }
    });
    document.addEventListener('change', event => {
        if (event.target.closest?.('#parsedCardsContainer')) saveSoon();
    });
    window.addEventListener('pagehide', flush);
    function initialize() {
        if (pdfCloudOnly()) hidePdfProjectPanels();
        restore();
        element('documentProjectsPanel')?.addEventListener('toggle', event => { if (event.currentTarget.open) refreshProjects(); });
        element('documentProjectsRefresh')?.addEventListener('click', refreshProjects);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
    else initialize();
})();
