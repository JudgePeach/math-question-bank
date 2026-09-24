// Sidebar Pagination & Sorting Global State
let currentBankPage = 1;
let currentDraftPage = 1;
const PAGE_LIMIT = 20;
let bankQuestionsLoadController = null;
let bankQuestionsLoadSequence = 0;
let bankQuestionsRetryTimer = null;

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
        function updateBankFilterSummary() {
            const button = document.querySelector('.bank-filter-toggle');
            const clear = document.getElementById('clearBankFiltersBtn');
            if (!button || !clear) return;
            const selected = ['filterCompulsory', 'filterChapter', 'filterKnowledge', 'filterType', 'filterDifficulty', 'filterSource']
                .map(id => document.getElementById(id)).filter(element => element && element.value);
            button.textContent = selected.length ? `筛选 (${selected.length})` : '筛选';
            button.setAttribute('data-tooltip', selected.map(element => element.tagName === 'SELECT' ? element.selectedOptions[0]?.textContent : element.value).join(' / ') || '按学段、章节、小节等条件筛选');
            clear.hidden = selected.length === 0;
        }
        function clearBankFilters() {
            ['filterCompulsory', 'filterChapter', 'filterKnowledge', 'filterType', 'filterDifficulty', 'filterSource'].forEach(id => {
                const element = document.getElementById(id);
                if (element) element.value = '';
            });
            document.getElementById('clearFilterSourceBtn')?.classList.add('hidden');
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

        function openQuestionEditorModal(panelId = 'classification') {
            const modal = document.getElementById('editorSection');
            const editButton = document.getElementById('editQuestionFromPreviewBtn');
            if (!modal) return;
            if (editButton && editButton.disabled && !modal.dataset.allowNewQuestion) {
                showToast('请先从题库中选择一道题目', 'info');
                return;
            }

            questionEditorRestoreFocus = document.activeElement;
            switchQuestionEditorPanel(panelId);
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
                openQuestionEditorModal('content');
                if (typeof switchContentTab === 'function') switchContentTab('manual');
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
            
            const draft = {
                id: EditorState.draftId || ('draft-' + Date.now()),
                content: content,
                question_type: qtype,
                category_compulsory: compulsory,
                category_chapter: chapter,
                category_knowledge: knowledge,
                difficulty: difficulty,
                source: source,
                answer_markdown: answerMarkdown,
                review: review,
                tags: tags,
                image_paths: Array.from(new Set([
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
            backupEditorState(null, draft.id);
            
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
            EditorState.useDraft(draft);
            
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
            
            if (draft.category_compulsory) {
                compSelect.value = draft.category_compulsory;
                compSelect.onchange();
                if (draft.category_chapter) {
                    chapSelect.value = draft.category_chapter;
                    chapSelect.onchange();
                    if (draft.category_knowledge) {
                        knowSelect.value = draft.category_knowledge;
                    }
                }
            }
            
            // Load images
            const allDraftImages = Array.isArray(draft.image_paths)
                ? draft.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            uploadedAnswerImages = typeof window.collectAnswerImagePaths === 'function'
                ? window.collectAnswerImagePaths(draft.answer_markdown || '')
                : [];
            uploadedImages = allDraftImages.filter(path => !uploadedAnswerImages.includes(path));
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
            
            // Backup draft state
            backupEditorState(null, draft.id);
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
            
            // Filter by compulsory
            if (compulsory) {
                drafts = drafts.filter(item => item.category_compulsory === compulsory);
            }
            
            // Filter by chapter
            if (chapter) {
                drafts = drafts.filter(item => item.category_chapter === chapter);
            }
            
            if (knowledge) drafts = drafts.filter(item => item.category_knowledge === knowledge);

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
                    <div class="text-xs text-slate-700 leading-relaxed font-medium line-clamp-2 card-formula-render">${cleanContent || '[未填题干]'}</div>
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
            };
            
            chapSelect.onchange = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (可不选，默认整章) --</option>';
                
                if (comp && chap && categoryTree[comp][chap]) {
                    knowSelect.disabled = false;
                    categoryTree[comp][chap].forEach(k => {
                        knowSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(k)}">${window.MathBankSafe.escapeText(k)}</option>`;
                    });
                } else {
                    knowSelect.disabled = true;
                }
            };

            // Restore backed up values if they exist in the new categoryTree
            if (selectedComp && categoryTree[selectedComp]) {
                compSelect.value = selectedComp;
                compSelect.onchange();
                if (selectedChap && categoryTree[selectedComp][selectedChap]) {
                    chapSelect.value = selectedChap;
                    chapSelect.onchange();
                    if (selectedKnow && categoryTree[selectedComp][selectedChap].includes(selectedKnow)) {
                        knowSelect.value = selectedKnow;
                    }
                }
            }
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
                if (activeSidebarTab === 'bank') loadQuestions();
                else loadDrafts();
            }
            fill(compSelect, Object.keys(categoryTree), '所有学段', previous[0]);
            fillChapters(previous[1], previous[2]);
            compSelect.onchange = () => { fillChapters(); reload(); };
            chapSelect.onchange = () => { fillKnowledge(); reload(); };
            knowSelect.onchange = reload;
            updateBankFilterSummary();
        }

        // Load and List Saved Questions
        function loadQuestions(retryCount = 0) {
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
                            <div class="bank-question-excerpt text-xs text-slate-700 leading-relaxed font-medium line-clamp-2 card-formula-render">${cleanContent || '[空白题干]'}</div>
                            <div class="bank-question-meta flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                                <span class="truncate max-w-[120px] font-semibold"><i class="fa-solid fa-folder-open mr-0.5"></i>${window.MathBankSafe.escapeText(item.category_knowledge || item.category_chapter || '未分类')}</span>
                                <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '本地录入')}</span>
                                <time class="bank-question-time" title="录入于 ${window.MathBankSafe.escapeText(formatChineseDate(item.created_at))}">${window.MathBankSafe.escapeText(String(item.created_at || '').slice(0, 10))}</time>
                            </div>
                        `;
                        
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
            sourceEl.textContent = `来源: ${editSource.value || '本地教研录入'}`;
        }
        window.renderEditorPaperMeta = renderEditorPaperMeta;

        const choicesGridResizeObservers = new WeakMap();

        function getPreferredChoicesColumns(grid) {
            const stored = parseInt(grid.dataset.preferredColumns || '', 10);
            if ([1, 2, 4].includes(stored)) return stored;
            if (grid.classList.contains('grid-cols-1')) return 1;
            if (grid.classList.contains('grid-cols-2')) return 2;
            return 4;
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

        function adaptSingleChoicesGrid(grid) {
            if (!grid || !grid.isConnected || grid.clientWidth <= 0) return;
            const preferred = getPreferredChoicesColumns(grid);
            const candidates = [4, 2, 1].filter(cols => cols <= preferred);
            let selected = 1;

            for (const columns of candidates) {
                grid.style.setProperty('--choices-columns', String(columns));
                // Force the browser to resolve the candidate track widths before
                // comparing each rendered KaTeX option's real scroll width.
                void grid.offsetWidth;
                if (!choicesGridOverflows(grid)) {
                    selected = columns;
                    break;
                }
            }

            grid.style.setProperty('--choices-columns', String(selected));
            grid.dataset.choiceColumns = String(selected);
        }

        function adaptChoicesGridLayout(root) {
            if (!root) return;
            const grids = [];
            if (root.matches && root.matches('.choices-grid')) grids.push(root);
            if (root.querySelectorAll) grids.push(...root.querySelectorAll('.choices-grid'));

            grids.forEach(grid => {
                adaptSingleChoicesGrid(grid);
                grid.querySelectorAll('img').forEach(img => {
                    if (!img.complete) {
                        img.addEventListener('load', () => adaptSingleChoicesGrid(grid), { once: true });
                    }
                });
                if (typeof ResizeObserver !== 'undefined' && !choicesGridResizeObservers.has(grid)) {
                    const observer = new ResizeObserver(() => {
                        window.requestAnimationFrame(() => adaptSingleChoicesGrid(grid));
                    });
                    observer.observe(grid);
                    choicesGridResizeObservers.set(grid, observer);
                }
            });
        }
        window.adaptChoicesGridLayout = adaptChoicesGridLayout;

        function setupRealtimePreviews() {
            const editContent = document.getElementById('editContent');
            const editAnswer = document.getElementById('editAnswerMarkdown');

            const updateContentPreview = () => {
                const text = editContent.value;
                const previewContainer = document.getElementById('contentPreview');
                const paperContainer = document.getElementById('paperContent');
                
                // Automatically sync illustrations list with text content
                if (uploadedImages.length > 0) {
                    uploadedImages = uploadedImages.filter(path => text.includes(path));
                    renderIllustrationBadges();
                }
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
                    return;
                }
                
                const preparedHtml = renderQuestionPreviewContent(previewContainer, text, { imageLayouts: FigureLayoutState.imageLayouts });
                renderQuestionPreviewContent(paperContainer, text, { preparedHtml: preparedHtml });
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
                
                let html = parseMarkdownWithMath(text);
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
                let html = parseMarkdownWithMath(text);
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

            const sanitizedDollars = cleaned.replace(/\\\$/g, '');
            const dollarCount = (sanitizedDollars.match(/\$/g) || []).length;
            if (dollarCount % 2 !== 0) {
                cleaned += '$';
            }

            return cleaned;
        }
        window.cleanChoiceStemParentheses = cleanChoiceStemParentheses;

        function transformFillinMacro(clean) {
            if (!clean) return "";
            return clean.replace(/(\$?)\\fillin(?:\[([^\]]*?)\])?(?:\[([^\]]*?)\])?(\$?)/g, function(match, preDollar, p1, p2, postDollar, offset, fullString) {
                // 计算当前 \fillin 之前未转义 $ 的数量
                const preString = fullString.substring(0, offset).replace(/\\\$/g, '');
                const dollarsBefore = (preString.match(/\$/g) || []).length;
                const isInsideMath = (dollarsBefore % 2 !== 0) || (preDollar === '$');
                
                function isLengthStr(str) {
                    return /^\s*\d+(?:\.\d+)?\s*(?:cm|mm|in|pt|pc|em|ex)\s*$/i.test(str || '');
                }

                let innerTex = '\\underline{\\hspace{1.5cm}}';
                if (p1 !== undefined && p2 !== undefined) {
                    const len = isLengthStr(p1) ? p1 : '1.5cm';
                    innerTex = '\\underline{\\hspace{' + len + '}' + p2 + '\\hspace{' + len + '}}';
                } else if (p1 !== undefined) {
                    if (isLengthStr(p1)) {
                        innerTex = '\\underline{\\hspace{' + p1 + '}}';
                    } else {
                        innerTex = '\\underline{\\quad ' + p1 + ' \\quad}';
                    }
                }

                // 检查从当前位置往后，本行内是否还有未转义的 $
                const postString = fullString.substring(offset + match.length).replace(/\\\$/g, '');
                const nextDollarIdx = postString.indexOf('$');
                const nextNewlineIdx = postString.indexOf('\n');
                const hasClosingDollarInLine = (nextDollarIdx !== -1 && (nextNewlineIdx === -1 || nextDollarIdx < nextNewlineIdx));

                if (isInsideMath) {
                    if (postDollar === '$') {
                        // 紧跟 postDollar 为 $ 时必须将闭合 $ 重新补回，绝对不能将其吞掉
                        return innerTex + '$';
                    } else if (hasClosingDollarInLine) {
                        return innerTex;
                    } else {
                        // 句末漏写闭合 $ 时自动补齐闭合
                        return innerTex + '$';
                    }
                } else {
                    // 处于非数学环境中：防错隔离！若后方紧跟着 $（如 \fillin$. 且后续文本以 $ 开头），避免产生双 $$
                    if (postDollar === '$' || postString.trim().startsWith('$')) {
                        return '$' + innerTex;
                    }
                    return '$' + innerTex + '$';
                }
            });
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

            [
                /```[\s\S]*?```/g,
                /`[^`\n]*`/g,
                /\\begin\{tikzpicture\}[\s\S]*?\\end\{tikzpicture\}/g,
                /!\[[^\]\n]*\]\([^\n)]*\)/g,
                /\[\[MBM_[A-Za-z0-9_:-]+\]\]/g,
                /\[ILLUSTRATION_BOX:\s*[^\]\n]*\]/gi,
                // Cells already contain these tokens when table rendering
                // reaches this helper. They must survive until final restore.
                /@@MATH_PLACEHOLDER_\d+@@/g
            ].forEach(function(pattern) { protect(pattern); });

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

        function isLatexTokenEscaped(text, index) {
            let cursor = index - 1;
            while (cursor >= 0 && text[cursor] === '\\') cursor--;
            return (index - cursor - 1) % 2 === 1;
        }

        function replaceDelimitedMathForPreview(text, replace) {
            const parts = [];
            let cursor = 0;
            let copied = 0;
            while (cursor < text.length) {
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

        function replaceLatexEnvironmentsForPreview(text, replace) {
            const recognized = /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr|equation\*?|align\*?|alignat\*?|gather\*?|multline\*?|displaymath|math|cases|aligned|alignedat|gathered|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix|array|split)$/;
            const tokenPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
            const parts = [];
            let copied = 0;
            let match;
            while ((match = tokenPattern.exec(text))) {
                const name = match[2];
                if (match[1] !== 'begin' || !recognized.test(name) || isLatexTokenEscaped(text, match.index)) continue;
                const bodyStart = tokenPattern.lastIndex;
                const nestedPattern = /\\(begin|end)\{([^{}\n]+)\}/g;
                nestedPattern.lastIndex = bodyStart;
                const stack = [name];
                let nested;
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
                        break;
                    }
                }
            }
            parts.push(text.slice(copied));
            return parts.join('');
        }
        window.normalizeNakedMathForPreview = normalizeNakedMathForPreview;

        function preprocessFormulaForKaTeX(text, imageLayouts = {}) {
            if (!text) return "";
            
            // Clean up any historical \vphantom{...} or \strut from underline text to prevent KaTeX rendering artifact letters
            let clean = text.replace(/\\vphantom\s*\{\s*[^}]*?\}/g, '')
                            .replace(/\\strut\b/g, '');

            clean = normalizeNakedMathForPreview(clean);

            // Auto-heal punctuation inside math environment between \fillin and closing dollar (e.g. $ \fillin, $ -> $ \fillin $,) using safe replacer function
            clean = clean.replace(/(\$[^$]*?\\fillin)\s*([。，,；;！？!?\.]+)\s*\$/g, function(match, p1, p2) {
                return p1 + '$' + p2;
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
            
            // If \underline{\hspace{...}} is directly exposed outside math environments, wrap it inside '$...$' so KaTeX scanner can parse it!
            clean = clean.replace(/(\$?)\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}(\$?)/g, function(match, p1, p2, p3, offset, fullString) {
                const preString = fullString.substring(0, offset).replace(/\\\$/g, '');
                const dollarsBefore = (preString.match(/\$/g) || []).length;
                if (dollarsBefore % 2 !== 0 || p1 === '$' || p3 === '$') {
                    return match;
                }
                return '$\\underline{\\hspace{' + p2 + '}}$';
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

            // Process choices environment (exam-zh-choices)
            tempText = tempText.replace(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/g, function(match, inner) {
                const items = inner.split(/\\item/).map(item => item.trim()).filter(item => item.length > 0);
                const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
                let maxLen = 0;
                const hasImages = items.some(item => /!\[[^\]]*\]\([^)]+\)/.test(item));
                const fourImageOptions = items.length === 4 && items.every(item =>
                    /^!\[[^\]]*\]\([^)]+\)$/.test(item.trim())
                );
                items.forEach(item => {
                    const approxText = item.replace(/!\[[^\]]*\]\([^)]+\)/g, '')
                        .replace(/@@MATH_PLACEHOLDER_\d+@@/g, '********');
                    if (approxText.length > maxLen) {
                        maxLen = approxText.length;
                    }
                });

                let gridCols = "grid-cols-4";
                if (maxLen > 24) {
                    gridCols = "grid-cols-1";
                } else if ((hasImages && !fourImageOptions) || maxLen > 10) {
                    gridCols = "grid-cols-2";
                }

                const preferredColumns = gridCols === 'grid-cols-1' ? 1 : (gridCols === 'grid-cols-2' ? 2 : 4);
                let html = `<div class="grid ${gridCols} gap-2 my-2 select-none choices-grid ${hasImages ? 'choices-has-images' : ''} items-baseline" data-preferred-columns="${preferredColumns}">`;
                items.forEach((item, idx) => {
                    const label = labels[idx] || (idx + 1);
                    let cleanItem = item;
                    // Auto-wrap LaTeX math macros (e.g. \dfrac{5}{2}) in choices option if missing $
                    if (/\\(dfrac|frac|sqrt|cdot|times|pm|le|ge|ne|in|vec|mathbf|boldsymbol|mathrm|text|alpha|beta|gamma|delta|theta|pi|varphi|omega)\b/.test(cleanItem) && !/\$/.test(cleanItem)) {
                        cleanItem = '$' + cleanItem + '$';
                    }
                    html += `<div class="choices-item flex items-baseline"><span class="choices-label font-bold mr-1.5 text-slate-800 shrink-0">${label}.</span><div class="choices-content flex-1 [&>p]:m-0 [&>p]:inline">${cleanItem}</div></div>`;
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
                               .replace(/\\quad/g, '&nbsp;&nbsp;');
                               
            // Replace LaTeX line breaks with HTML br tags outside math environments
            tempText = tempText.replace(/\\\\/g, '<br>');
            
            // A paragraph starts a new line with a short visual gap; two BRs
            // would additionally create a whole empty text line in Word answers.
            const paragraphBreak = '<span class="mb-preview-paragraph-break" aria-hidden="true"></span>';
            // Keep subquestions on distinct paragraphs without adding a second
            // gap when the source already has a blank line or a hard break.
            tempText = tempText.replace(/(?:\r?\n|\s+|[。；;!！\.]\s*)([(（]?(?:[1-9]|10|[ivxIVX]+|[①②③④⑤⑥⑦⑧⑨⑩])[)）\.]|\([1-9]\)|（[1-9]）|\([ivxIVX]+\)|（[ivxIVX]+）)(?=\s*[\u4e00-\u9fa5a-zA-Z\$])/g, paragraphBreak + '$1 ');

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
            
            return tempText;
        }
        window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;
            
        function parseMarkdownWithMath(text, imageLayouts = {}) {
            if (!text) return "";
            return window.MathBankSafe.sanitizeRichHtml(preprocessFormulaForKaTeX(text, imageLayouts));
        }
        window.parseMarkdownWithMath = parseMarkdownWithMath;

        function renderQuestionPreviewContent(container, text, options = {}) {
            if (!container) return;
            const settings = options && typeof options === 'object' ? options : {};
            const includeImages = settings.includeImages !== false;
            let preparedHtml;
            if (typeof settings.preparedHtml === 'string') {
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
                preparedHtml = parseMarkdownWithMath(source, settings.imageLayouts || {});
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
