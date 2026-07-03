// Sidebar Pagination & Sorting Global State
let currentBankPage = 1;
let currentDraftPage = 1;
const PAGE_LIMIT = 20;

        function initResizers() {
            const sidebar = document.getElementById('sidebarSection');
            const editor = document.getElementById('editorSection');
            const preview = document.getElementById('previewSection');
            const resizer1 = document.getElementById('resizer-1');
            const resizer2 = document.getElementById('resizer-2');
            const resizerV = document.getElementById('sidebar-resizer-v');
            const sidebarTopPanel = document.getElementById('sidebarTopPanel');
            const mainContainer = document.querySelector('main');

            let isResizingLeft = false;
            let isResizingRight = false;
            let isResizingV = false;

            resizer1.addEventListener('mousedown', function(e) {
                e.preventDefault();
                isResizingLeft = true;
                document.body.style.cursor = 'col-resize';
                document.body.classList.add('select-none');
            });

            resizer2.addEventListener('mousedown', function(e) {
                e.preventDefault();
                isResizingRight = true;
                document.body.style.cursor = 'col-resize';
                document.body.classList.add('select-none');
            });

            if (resizerV && sidebarTopPanel) {
                resizerV.addEventListener('mousedown', function(e) {
                    e.preventDefault();
                    isResizingV = true;
                    document.body.style.cursor = 'row-resize';
                    document.body.classList.add('select-none');
                });
            }

            document.addEventListener('mousemove', function(e) {
                if (!isResizingLeft && !isResizingRight && !isResizingV) return;

                const containerRect = mainContainer.getBoundingClientRect();

                if (isResizingLeft) {
                    let newWidth = e.clientX - containerRect.left;
                    if (newWidth < 45) {
                        newWidth = 0;
                        sidebar.style.width = '0px';
                        sidebar.style.minWidth = '0px';
                        sidebar.style.borderRightWidth = '0px';
                    } else {
                        sidebar.style.borderRightWidth = '1px';
                        if (newWidth > containerRect.width * 0.5) {
                            newWidth = containerRect.width * 0.5;
                        }
                        sidebar.style.width = newWidth + 'px';
                    }
                }

                if (isResizingRight) {
                    let newWidth = containerRect.right - e.clientX;
                    if (newWidth < 45) {
                        newWidth = 0;
                        preview.style.width = '0px';
                        preview.style.minWidth = '0px';
                        preview.style.borderLeftWidth = '0px';
                    } else {
                        preview.style.borderLeftWidth = '1px';
                        if (newWidth > containerRect.width * 0.5) {
                            newWidth = containerRect.width * 0.5;
                        }
                        preview.style.width = newWidth + 'px';
                    }
                }

                if (isResizingV && sidebar && sidebarTopPanel) {
                    const sidebarRect = sidebar.getBoundingClientRect();
                    let newHeight = e.clientY - sidebarRect.top;
                    const minHeight = 100;
                    const maxHeight = sidebarRect.height * 0.85;

                    if (newHeight < minHeight) {
                        newHeight = minHeight;
                    } else if (newHeight > maxHeight) {
                        newHeight = maxHeight;
                    }
                    sidebarTopPanel.style.height = newHeight + 'px';
                }
            });

            document.addEventListener('mouseup', function() {
                if (isResizingLeft || isResizingRight || isResizingV) {
                    isResizingLeft = false;
                    isResizingRight = false;
                    isResizingV = false;
                    document.body.style.cursor = '';
                    document.body.classList.remove('select-none');
                    window.dispatchEvent(new Event('resize'));
                }
            });
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

        // Helper to backup the current editor state directly from the DOM elements
        function backupEditorState(id = null, draftId = null) {
            originalQuestionState = {
                id: id,
                draftId: draftId,
                content: document.getElementById('editContent').value,
                answer_markdown: document.getElementById('editAnswerMarkdown').value,
                review: document.getElementById('editReview').value,
                question_type: document.getElementById('editQType').value,
                difficulty: document.getElementById('editDifficulty').value,
                source: document.getElementById('editSource').value,
                category_compulsory: document.getElementById('editCompulsory').value,
                category_chapter: document.getElementById('editChapter').value,
                category_knowledge: document.getElementById('editKnowledge').value,
                image_paths: JSON.stringify(uploadedImages),
                tags: document.getElementById('editTags') ? document.getElementById('editTags').value : ''
            };
        }

        // Helper to check if the current question has been modified from its original loaded state
        function isEditorModified() {
            if (!originalQuestionState) return false;
            
            const currentContent = document.getElementById('editContent').value;
            const currentAnswer = document.getElementById('editAnswerMarkdown').value;
            const currentReview = document.getElementById('editReview').value;
            const currentType = document.getElementById('editQType').value;
            const currentDifficulty = document.getElementById('editDifficulty').value;
            const currentSource = document.getElementById('editSource').value;
            const currentComp = document.getElementById('editCompulsory').value;
            const currentChap = document.getElementById('editChapter').value;
            const currentKnow = document.getElementById('editKnowledge').value;
            const currentImages = JSON.stringify(uploadedImages);
            const currentTags = document.getElementById('editTags') ? document.getElementById('editTags').value : '';
            
            return currentContent !== originalQuestionState.content ||
                   currentAnswer !== originalQuestionState.answer_markdown ||
                   currentReview !== originalQuestionState.review ||
                   currentType !== originalQuestionState.question_type ||
                   currentDifficulty !== originalQuestionState.difficulty ||
                   currentSource !== originalQuestionState.source ||
                   currentComp !== originalQuestionState.category_compulsory ||
                   currentChap !== originalQuestionState.category_chapter ||
                   currentKnow !== originalQuestionState.category_knowledge ||
                   currentImages !== originalQuestionState.image_paths ||
                   currentTags !== originalQuestionState.tags;
        }

        // Custom Premium Confirmation Modal for Unsaved Changes (3 Options)
        function showUnsavedChangesModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none";
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl p-6 space-y-4 transform scale-100 transition-all border border-slate-100">
                        <div class="flex items-center space-x-2 pb-2 border-b">
                            <i class="fa-solid fa-circle-question text-brand-600 text-base animate-pulse"></i>
                            <h3 class="font-bold text-sm text-slate-800">当前编辑内容有未保存的修改</h3>
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
                            <button id="cancelBtn" type="button" class="px-4 py-1.5 border border-slate-250 rounded-xl text-slate-550 hover:bg-slate-50 transition-all text-[10px] font-medium">
                                返回编辑
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                document.getElementById('saveToBankBtn').onclick = () => {
                    document.body.removeChild(modalDiv);
                    resolve('bank');
                };

                document.getElementById('saveToDraftsBtn').onclick = () => {
                    document.body.removeChild(modalDiv);
                    resolve('drafts');
                };

                document.getElementById('discardBtn').onclick = () => {
                    document.body.removeChild(modalDiv);
                    resolve('discard');
                };

                document.getElementById('cancelBtn').onclick = () => {
                    document.body.removeChild(modalDiv);
                    resolve('cancel');
                };
            });
        }

        // Custom Premium Confirmation Modal for Missing School Phase (Compulsory)
        function showMissingCompulsoryModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none opacity-0 transition-opacity duration-300";
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4 transform scale-95 transition-all duration-300 border border-slate-100/55">
                        <div class="flex items-center space-x-2.5 pb-2.5 border-b border-slate-100">
                            <div class="w-8 h-8 rounded-xl bg-brand-50 flex items-center justify-center">
                                <i class="fa-solid fa-wand-magic-sparkles text-brand-600 text-sm animate-pulse"></i>
                            </div>
                            <div>
                                <h3 class="font-bold text-sm text-slate-800">题目分类信息不完整</h3>
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
                    modalDiv.classList.add('opacity-0');
                    modalDiv.querySelector('div').classList.remove('scale-100');
                    modalDiv.querySelector('div').classList.add('scale-95');
                    setTimeout(() => {
                        document.body.removeChild(modalDiv);
                        resolve(result);
                    }, 300);
                };

                document.getElementById('manualCompulsoryBtn').onclick = () => closeModal('manual');
                document.getElementById('autoSaveClassifyBtn').onclick = () => closeModal('ai');
                document.getElementById('cancelCompulsoryBtn').onclick = () => closeModal('cancel');
            });
        }

        // Check if editor has unsaved changes, show modal if needed, then run callback
        async function checkAndSwitch(actionCallback) {
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
                id: currentDraftId || ('draft-' + Date.now()),
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
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : [])
                ])),
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
            currentDraftId = draft.id;
            
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
            currentQuestionId = null;
            currentSeqNum = null;
            currentDraftId = draft.id;
            
            // Populate form fields
            document.getElementById('editContent').value = draft.content || '';
            document.getElementById('editQType').value = draft.question_type || 'single_choice';
            document.getElementById('editDifficulty').value = draft.difficulty || 'easy_error';
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
            uploadedImages = draft.image_paths || [];
            renderIllustrationBadges();
            
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
            
            document.getElementById('editorTitle').textContent = `编辑草稿 - 暂存中`;
            
            // Backup draft state
            backupEditorState(null, draft.id);
            
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
            const qListContainer = document.getElementById('questionsList');
            const q = document.getElementById('searchInput').value.trim().toLowerCase();
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const compulsory = document.getElementById('filterCompulsory').value;
            const chapter = document.getElementById('filterChapter').value;
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
                    <div class="p-6 text-center text-slate-400 text-xs">
                        <i class="fa-solid fa-box-open text-2xl mb-1 text-slate-350"></i>
                        <p>草稿箱空空如也</p>
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
                
                const isActive = currentDraftId === item.id;
                itemCard.className = `p-3.5 mx-1.5 rounded-xl border glass-card hover:bg-white cursor-pointer transition-all duration-200 shadow-sm flex flex-col space-y-2 select-none group relative ${isActive ? 'border-emerald-500 bg-white ring-2 ring-emerald-100 shadow-md' : 'border-slate-200'}`;
                
                const cleanContent = preprocessFormulaForKaTeX(item.content || '');
                
                let tagsHtml = '';
                if (item.tags) {
                    const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                    if (tagList.length > 0) {
                        const displayTags = tagList.slice(0, 2);
                        const hiddenCount = tagList.length - 2;
                        
                        displayTags.forEach(tag => {
                            tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-250/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${tag}</span></span>`;
                        });
                        
                        if (hiddenCount > 0) {
                            const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${tag}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
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
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 shrink-0 mt-0.5">草稿 • ${typeText}</span>
                        <div class="flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                            ${tagsHtml}
                            ${difficultyBadge}
                            <!-- Delete Button -->
                            <button onclick="event.stopPropagation(); deleteDraft('${item.id}')" class="text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除草稿">
                                <i class="fa-solid fa-trash-can text-[10px]"></i>
                            </button>
                        </div>
                    </div>
                    <div class="text-xs text-slate-700 leading-relaxed font-medium line-clamp-2 card-formula-render">${cleanContent || '[未填题干]'}</div>
                    <div class="flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                        <span class="truncate max-w-[120px] font-semibold text-emerald-600"><i class="fa-solid fa-box mr-0.5"></i>${item.category_knowledge || item.category_chapter || '未分类'}</span>
                        <span class="font-mono text-slate-400">${item.source ? item.source.substring(0, 12) : '草稿暂存'}</span>
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
                    checkAndSwitch(() => selectDraft(item));
                };
                
                qListContainer.appendChild(itemCard);
            });
            
            renderSidebarPagination(totalItems, currentDraftPage, 'drafts');
        }

        function deleteDraft(id) {
            if (confirm('确认要删除这篇草稿吗？')) {
                let drafts = getLocalStorageDrafts();
                drafts = drafts.filter(d => d.id !== id);
                setLocalStorageDrafts(drafts);
                
                showToast('草稿已删除！');
                updateDraftCountBadge();
                
                if (currentDraftId === id) {
                    // Reset current draft state
                    currentDraftId = null;
                    startNewQuestionWithoutPrompt();
                }
                
                if (activeSidebarTab === 'drafts') {
                    loadDrafts();
                }
            }
        }

        function openStatsModal() {
            const modal = document.getElementById('statsModal');
            
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
                });
        }

        function closeStatsModal() {
            const modal = document.getElementById('statsModal');
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
                    compSelect.innerHTML += `<option value="${comp}">${comp}</option>`;
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
                    chapSelect.innerHTML += `<option value="${chap}">${chap}</option>`;
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
                    listContainer.innerHTML = '<div class="text-[10px] text-slate-450 text-center py-4">本章暂无细分知识点</div>';
                } else {
                    Object.entries(knowStats).forEach(([know, knCount]) => {
                        const pct = Math.round((knCount / count) * 100);
                        listContainer.innerHTML += `
                            <div class="space-y-1 bg-slate-50/70 p-2 rounded-lg border border-slate-100">
                                <div class="flex justify-between items-center text-[10px] font-semibold text-slate-700">
                                    <span class="truncate pr-2">${know}</span>
                                    <span class="font-mono text-slate-550 text-[10px]">${knCount} 题 (${pct}%)</span>
                                </div>
                                <div class="w-full bg-slate-200 h-1.5 rounded-full overflow-hidden">
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
                emptyCell.className = "bg-slate-100/30 rounded-lg border border-transparent";
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
                    dayCell.className = `p-1 bg-red-50/70 border border-red-200/50 hover:bg-red-100/50 rounded-lg flex flex-col justify-between items-center transition-all shadow-sm cursor-help select-none ${isToday ? 'ring-2 ring-red-400' : ''}`;
                    dayCell.title = `当天最终录入：${count} 道题目`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-bold text-red-800 ${isToday ? 'bg-red-200 px-1 py-0.5 rounded-md' : ''}">${day}</span>
                        <span class="text-[10px] font-extrabold text-red-500 font-mono animate-[bounce_1.5s_infinite]">+${count}</span>
                    `;
                } else {
                    dayCell.className = `p-1 bg-white border border-slate-200/40 hover:bg-slate-50 rounded-lg flex flex-col justify-start items-center transition-all select-none ${isToday ? 'ring-2 ring-brand-400 border-brand-300' : ''}`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-medium text-slate-550 ${isToday ? 'bg-brand-100 text-brand-700 px-1 py-0.5 rounded-md font-bold' : ''}">${day}</span>
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
                    emptyCell.className = "bg-slate-100/30 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            } else {
                for (let i = 0; i < remainingCells; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 rounded-lg border border-transparent";
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
                compSelect.innerHTML += `<option value="${c}">${c}</option>`;
            });
            
            compSelect.onchange = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;
                
                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        chapSelect.innerHTML += `<option value="${ch}">${ch}</option>`;
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
                        knowSelect.innerHTML += `<option value="${k}">${k}</option>`;
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
            
            if (!compSelect || !chapSelect) return;
            if (!categoryTree || typeof categoryTree !== 'object') {
                console.warn('[Security Shield] 分类数据未准备完毕，跳过过滤框分类级联填充');
                return;
            }
            
            compSelect.innerHTML = '<option value="">所有学段/必选修</option>';
            Object.keys(categoryTree).forEach(c => {
                compSelect.innerHTML += `<option value="${c}">${c}</option>`;
            });
            
            compSelect.onchange = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">所有章节</option>';
                
                if (comp && categoryTree[comp]) {
                    chapSelect.classList.remove('hidden');
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        chapSelect.innerHTML += `<option value="${ch}">${ch}</option>`;
                    });
                } else {
                    chapSelect.classList.add('hidden');
                }
                
                // Reset page numbers
                currentBankPage = 1;
                currentDraftPage = 1;
                
                if (activeSidebarTab === 'bank') {
                    loadQuestions(); // Refilter
                } else {
                    loadDrafts(); // Refilter
                }
            };
            
            chapSelect.onchange = () => {
                // Reset page numbers
                currentBankPage = 1;
                currentDraftPage = 1;
                
                if (activeSidebarTab === 'bank') {
                    loadQuestions(); // Refilter
                } else {
                    loadDrafts(); // Refilter
                }
            };
        }

        // Load and List Saved Questions
        function loadQuestions(retryCount = 0) {
            const q = document.getElementById('searchInput').value;
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const compulsory = document.getElementById('filterCompulsory').value;
            const chapter = document.getElementById('filterChapter').value;
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value : '';
            
            const params = new URLSearchParams();
            if (q) params.append('q', q);
            if (qtype) params.append('qtype', qtype);
            if (difficulty) params.append('difficulty', difficulty);
            if (compulsory) params.append('compulsory', compulsory);
            if (chapter) params.append('chapter', chapter);
            if (source) params.append('source', source);
            
            const qListContainer = document.getElementById('questionsList');
            
            fetch(`/api/questions?${params.toString()}`)
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(questions => {
                    qListContainer.innerHTML = '';
                    
                    if (questions.length === 0) {
                        qListContainer.innerHTML = `
                            <div class="p-6 text-center text-slate-400 text-xs">
                                <i class="fa-solid fa-box-open text-2xl mb-1 text-slate-350"></i>
                                <p>未找到匹配题目</p>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        return;
                    }
                    
                    // Sort Questions by time
                    const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
                    questions.sort((a, b) => {
                        const dateA = a.created_at ? new Date(a.created_at).getTime() : 0;
                        const dateB = b.created_at ? new Date(b.created_at).getTime() : 0;
                        if (dateA !== dateB) {
                            return sortOrder === 'asc' ? dateA - dateB : dateB - dateA;
                        }
                        return sortOrder === 'asc' ? a.id - b.id : b.id - a.id;
                    });
                    
                    const totalItems = questions.length;
                    const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
                    if (currentBankPage > totalPages) {
                        currentBankPage = totalPages;
                    }
                    if (currentBankPage < 1) {
                        currentBankPage = 1;
                    }
                    
                    const pageItems = questions.slice((currentBankPage - 1) * PAGE_LIMIT, currentBankPage * PAGE_LIMIT);
                    
                    pageItems.forEach(item => {
                        // Create card element
                        const difficultyBadge = getDifficultyBadge(item.difficulty);
                        const typeText = getTypeText(item.question_type);
                        
                        const itemCard = document.createElement('div');
                        itemCard.className = `question-card p-3.5 mx-1.5 flex flex-col space-y-2 select-none group relative ${currentQuestionId === item.id ? 'active' : ''}`;
                        itemCard.dataset.id = item.id;
                        
                        const cleanContent = preprocessFormulaForKaTeX(item.content || '');
                        
                        let tagsHtml = '';
                        if (item.tags) {
                            const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                            if (tagList.length > 0) {
                                const displayTags = tagList.slice(0, 2);
                                const hiddenCount = tagList.length - 2;
                                
                                displayTags.forEach(tag => {
                                    tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-250/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${tag}</span></span>`;
                                });
                                
                                if (hiddenCount > 0) {
                                    const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${tag}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
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
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0 mt-0.5">${typeText}</span>
                                <div class="flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2">
                                    ${tagsHtml}
                                    ${difficultyBadge}
                                    <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded bg-brand-50 text-brand-600 shadow-sm">#${item.seq_num}</span>
                                    <!-- Delete Button -->
                                    <button onclick="event.stopPropagation(); deleteQuestion(${item.id})" class="text-slate-400 hover:text-red-500 p-0.5 rounded hover:bg-slate-100 transition-all opacity-0 group-hover:opacity-100" title="删除">
                                        <i class="fa-solid fa-trash-can text-[10px]"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="text-xs text-slate-700 leading-relaxed font-medium line-clamp-2 card-formula-render">${cleanContent || '[空白题干]'}</div>
                            <!-- Time Badge -->
                            <div class="text-[8px] text-slate-400/80 flex items-center space-x-1 py-0.5">
                                <i class="fa-regular fa-clock text-[8px]"></i>
                                <span>录入：${formatChineseDate(item.created_at)}</span>
                            </div>
                            <div class="flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                                <span class="truncate max-w-[120px] font-semibold"><i class="fa-solid fa-folder-open mr-0.5"></i>${item.category_knowledge || item.category_chapter || '未分类'}</span>
                                <span class="font-mono text-slate-400">${item.source ? item.source.substring(0, 12) : '本地录入'}</span>
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
                    
                    renderSidebarPagination(totalItems, currentBankPage, 'bank');
                })
                .catch(err => {
                    console.error('加载题库列表发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载题库数据...`);
                        setTimeout(() => loadQuestions(retryCount + 1), 1500);
                    } else {
                        qListContainer.innerHTML = `
                            <div class="p-6 text-center text-red-500 text-xs">
                                <i class="fa-solid fa-triangle-exclamation text-2xl mb-1 text-red-400"></i>
                                <p class="font-semibold">获取题库列表失败</p>
                                <p class="text-[10px] text-slate-450 mt-0.5 mb-2.5">后台服务正在启动或连接超时</p>
                                <button onclick="loadQuestions()" class="px-3.5 py-1.5 bg-red-50 hover:bg-red-100 text-red-600 font-bold rounded-xl transition-all border border-red-200 hover:scale-95 text-[10px] inline-flex items-center space-x-1 cursor-pointer">
                                    <i class="fa-solid fa-arrows-rotate"></i><span>重新加载</span>
                                </button>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        showToast('系统正在连接或初始化后台，加载题库失败，请稍后刷新重试', 'error');
                    }
                });
        }

        // ==========================================
        //       SIDEBAR PAGINATION SYSTEM HELPERS
        // ==========================================
        function renderSidebarPagination(totalItems, currentPage, tabType) {
            const container = document.getElementById('sidebarPagination');
            if (!container) return;
            
            if (totalItems === 0) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.style.display = 'flex';
            
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            
            // Build pages array with sliding window folding
            let pages = [];
            if (totalPages <= 5) {
                for (let i = 1; i <= totalPages; i++) {
                    pages.push(i);
                }
            } else {
                pages.push(1);
                
                let start = Math.max(2, currentPage - 1);
                let end = Math.min(totalPages - 1, currentPage + 1);
                
                if (currentPage <= 3) {
                    end = 4;
                }
                if (currentPage >= totalPages - 2) {
                    start = totalPages - 3;
                }
                
                if (start > 2) {
                    pages.push('...');
                }
                
                for (let i = start; i <= end; i++) {
                    pages.push(i);
                }
                
                if (end < totalPages - 1) {
                    pages.push('...');
                }
                
                pages.push(totalPages);
            }
            
            let pagesHtml = '';
            pages.forEach(p => {
                if (p === '...') {
                    pagesHtml += `<span class="pagination-ellipsis">...</span>`;
                } else {
                    pagesHtml += `<button class="pagination-btn ${p === currentPage ? 'active' : ''}" onclick="goToSidebarPage(${p}, '${tabType}')">${p}</button>`;
                }
            });
            
            container.innerHTML = `
                <div class="flex items-center justify-between text-[10px] text-slate-400 font-semibold px-0.5">
                    <span>共 ${totalItems} 题 / ${totalPages} 页</span>
                    <div class="flex items-center space-x-1">
                        <span>跳转至</span>
                        <input type="number" min="1" max="${totalPages}" value="${currentPage}" class="pagination-jump-input" onkeydown="if(event.key==='Enter') jumpToSidebarPage(this.value, ${totalPages}, '${tabType}')">
                        <span>页</span>
                    </div>
                </div>
                <div class="flex items-center justify-center space-x-1">
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === 1 ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage - 1}, '${tabType}')">
                        <i class="fa-solid fa-chevron-left text-[9px] mr-0.5"></i>上一页
                    </button>
                    ${pagesHtml}
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage + 1}, '${tabType}')">
                        下一页<i class="fa-solid fa-chevron-right text-[9px] ml-0.5"></i>
                    </button>
                </div>
            `;
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
        function setupRealtimePreviews() {
            const editContent = document.getElementById('editContent');
            const editAnswer = document.getElementById('editAnswerMarkdown');

            const updateContentPreview = () => {
                const text = editContent.value;
                const previewContainer = document.getElementById('contentPreview');
                const paperContainer = document.getElementById('paperContent');
                
                // Automatically sync illustrations list with text content
                if (uploadedImages.length > 0) {
                    const initialLength = uploadedImages.length;
                    uploadedImages = uploadedImages.filter(path => text.includes(path));
                    if (uploadedImages.length !== initialLength) {
                        renderIllustrationBadges();
                    }
                }
                
                if (!text.trim()) {
                    previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将自动展示为极致精美的数学试卷排版格式。</p>';
                    return;
                }
                
                // Formatted content (standard Markdown with protected LaTeX to HTML)
                let html = parseMarkdownWithMath(text);
                
                previewContainer.innerHTML = html;
                paperContainer.innerHTML = html;
                
                // Trigger KaTeX render
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
                    console.error('KaTeX rendering error: ', e);
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
            
            // Sync side headings dynamically
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            
            const editTags = document.getElementById('editTags');
            
            const updatePaperMeta = () => {
                const badges = document.getElementById('paperBadges');
                const sourceEl = document.getElementById('paperFooterSource');
                
                let seqBadge = '';
                if (currentSeqNum) {
                    seqBadge = `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">编号：#${currentSeqNum}</span>`;
                } else {
                    seqBadge = `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-150 text-slate-500">编号：新题目</span>`;
                }
                
                let paperTagsHtml = '';
                const tagsVal = editTags ? editTags.value.trim() : '';
                if (tagsVal) {
                    const tagList = tagsVal.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                    tagList.forEach(tag => {
                        paperTagsHtml += `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-250/60 flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[8px] text-amber-500 mr-1"></i>${tag}</span>`;
                    });
                }
                
                badges.innerHTML = `
                    ${seqBadge}
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-brand-50 text-brand-700">题型：${getTypeText(editQType.value)}</span>
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">难度：${getDifficultyText(editDifficulty.value)}</span>
                    ${paperTagsHtml}
                `;
                sourceEl.textContent = `来源: ${editSource.value || '本地教研录入'}`;
            };
            
            editQType.addEventListener('change', updatePaperMeta);
            editDifficulty.addEventListener('change', updatePaperMeta);
            editSource.addEventListener('input', updatePaperMeta);
            if (editTags) editTags.addEventListener('input', updatePaperMeta);
            
            // Initial render of meta badges
            updatePaperMeta();
        }

        function preprocessFormulaForKaTeX(text) {
            if (!text) return "";
            
            // Clean up illegal nesting like \underline{\quad $\mathbf{14}$ \quad} in KaTeX
            let clean = text.replace(/(\\underline\s*\{[^}]*?)\$([^$]+?)\$([^}]*?\})/g, function(match, p1, p2, p3) {
                return '$' + p1 + p2 + p3 + '$';
            });
            
            // Auto-heal empty underline spacers like \underline{\hspace{2cm}} to draw visible lines via \vphantom{A}
            clean = clean.replace(/\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}/g, '\\underline{\\vphantom{A}\\hspace{$1}}');
            
            // If \underline{\vphantom{A}\hspace{...}} is directly exposed outside math environments, wrap it inside '$...$' so KaTeX scanner can parse it!
            clean = clean.replace(/(\$?)\\underline\s*\{\s*\\vphantom\s*\{\s*\w+\s*\}\s*\\hspace\s*\{([^}]+?)\}\s*\}(\$?)/g, function(match, p1, p2, p3) {
                if (p1 === '$' || p3 === '$') {
                    return match;
                }
                return '$\\underline{\\vphantom{A}\\hspace{' + p2 + '}}$';
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
            tempText = tempText.replace(/\$\$([\s\S]*?)\$\$/g, savePlaceholder)
                               .replace(/\\\[([\s\S]*?)\\\]/g, savePlaceholder)
                               .replace(/\\\(([\s\S]*?)\\\)/g, savePlaceholder)
                               .replace(/\$([^\$]+?)\$/g, savePlaceholder);
            
            // Strip HTML tags from non-math parts
            tempText = tempText.replace(/<[^>]*>/g, '');
            
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
                items.forEach(item => {
                    const approxText = item.replace(/@@MATH_PLACEHOLDER_\d+@@/g, '********');
                    if (approxText.length > maxLen) {
                        maxLen = approxText.length;
                    }
                });

                let gridCols = "grid-cols-4";
                if (maxLen > 24) {
                    gridCols = "grid-cols-1";
                } else if (maxLen > 10) {
                    gridCols = "grid-cols-2";
                }

                let html = `<div class="grid ${gridCols} gap-2 my-2 select-none">`;
                items.forEach((item, idx) => {
                    const label = labels[idx] || (idx + 1);
                    html += `<div class="flex items-start"><span class="font-bold mr-1.5 text-slate-800">${label}.</span><span class="flex-1">${item}</span></div>`;
                });
                html += '</div>';
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
                               
            // Restore math blocks with HTML escaping
            function escapeHtml(str) {
                return str.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;');
            }

            placeholders.forEach(({ placeholder, original }) => {
                tempText = tempText.replace(placeholder, () => escapeHtml(original));
            });
            
            return tempText;
        }
            
        function parseMarkdownWithMath(text) {
            if (!text) return "";
            
            // 1. Clean up redundant LaTeX line breaks surrounding environments/items to avoid blank block lines
            text = text.replace(/\\\\\s*\\begin\{/g, '\\begin{')
                       .replace(/\\begin\{([^}]+?)\}\s*\\\\/g, '\\begin{$1}')
                       .replace(/\\\\\s*\\end\{/g, '\\end{')
                       .replace(/\\end\{([^}]+?)\}\s*\\\\/g, '\\end{$1}')
                       .replace(/\\\\\s*\\item/g, '\\item')
                       .replace(/\\item\s*\\\\/g, '\\item');

            // 2. Convert standard LaTeX layout environments dynamically to HTML for beautiful rendering
            text = text.replace(/\\begin\{center\}/g, '<div class="text-center my-2">')
                       .replace(/\\end\{center\}/g, '</div>')
                       // Custom label lists like \item[A.]
                       .replace(/\\item\s*\[([^\]]+?)\]/g, '</li><li class="my-1 list-none -ml-5">$1 ')
                       // Standard lists items
                       .replace(/\\item/g, '</li><li class="my-1">')
                       // List blocks opening & closing
                       .replace(/\\begin\{itemize\}/g, '<ul class="list-disc pl-5 my-2">')
                       .replace(/\\begin\{enumerate\}/g, '<ol class="list-decimal pl-5 my-2">')
                       .replace(/\\end\{itemize\}/g, '</li></ul>')
                       .replace(/\\end\{enumerate\}/g, '</li></ol>')
                       // Strip trailing </li> at the start of lists gracefully
                       .replace(/<ul class="list-disc pl-5 my-2">\s*<\/li>/g, '<ul class="list-disc pl-5 my-2">')
                       .replace(/<ol class="list-decimal pl-5 my-2">\s*<\/li>/g, '<ol class="list-decimal pl-5 my-2">');
            
            // Clean up illegal nesting like \underline{\quad $\mathbf{14}$ \quad} in KaTeX
            text = text.replace(/(\\underline\s*\{[^}]*?)\$([^$]+?)\$([^}]*?\})/g, function(match, p1, p2, p3) {
                return '$' + p1 + p2 + p3 + '$';
            });
            
            // Auto-heal empty underline spacers like \underline{\hspace{2cm}} to draw visible lines via \vphantom{A}
            text = text.replace(/\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}/g, '\\underline{\\vphantom{A}\\hspace{$1}}');
            
            // If \underline{\vphantom{A}\hspace{...}} is directly exposed outside math environments, wrap it inside '$...$' so KaTeX scanner can parse it!
            text = text.replace(/(\$?)\\underline\s*\{\s*\\vphantom\s*\{\s*\w+\s*\}\s*\\hspace\s*\{([^}]+?)\}\s*\}(\$?)/g, function(match, p1, p2, p3) {
                if (p1 === '$' || p3 === '$') {
                    return match;
                }
                return '$\\underline{\\vphantom{A}\\hspace{' + p2 + '}}$';
            });
            
            const placeholders = [];
            let placeholderCounter = 0;
            
            function savePlaceholder(match) {
                const placeholder = `@@MATH_PLACEHOLDER_${placeholderCounter++}@@`;
                placeholders.push({ placeholder, original: match });
                return placeholder;
            }
            
            let tempText = text;
            
            // 1. Protect block math $$...$$
            tempText = tempText.replace(/\$\$([\s\S]*?)\$\$/g, savePlaceholder);
            
            // 2. Protect block math \[...\]
            tempText = tempText.replace(/\\\[([\s\S]*?)\\\]/g, savePlaceholder);
            
            // 3. Protect inline math \(...\)
            tempText = tempText.replace(/\\\(([\s\S]*?)\\\)/g, savePlaceholder);
            
            // 4. Protect inline math $...$
            tempText = tempText.replace(/\$([^\$]+?)\$/g, savePlaceholder);
            
            // Replace standard LaTeX spacing commands outside math environments with HTML spaces (supports both single and double backslashes)
            tempText = tempText.replace(/\\\\qquad/g, '<span style="display:inline-block; width:2em;"></span>')
                               .replace(/\\\\quad/g, '<span style="display:inline-block; width:1em;"></span>')
                               .replace(/\\qquad/g, '<span style="display:inline-block; width:2em;"></span>')
                               .replace(/\\quad/g, '<span style="display:inline-block; width:1em;"></span>');
            
            // Process LaTeX bold formatting outside math environments
            tempText = tempText.replace(/\\textbf\s*\{([^{}]*?)\}/g, '<strong>$1</strong>');

            // Replace all non-math LaTeX line breaks with HTML br tags
            tempText = tempText.replace(/\\\\/g, '<br>');
            
            // 5. Parse markdown with marked and sanitize with DOMPurify for XSS Protection
            let html = DOMPurify.sanitize(marked.parse(tempText));
            
            // 6. Restore all math blocks literally with HTML escaping
            function escapeHtml(str) {
                return str.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;');
            }

            placeholders.forEach(({ placeholder, original }) => {
                html = html.replace(placeholder, () => escapeHtml(original));
            });
            
            return html;
        }

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

