        function startNewQuestionWithoutPrompt() {
            currentQuestionId = null;
            currentDraftId = null;
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            
            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            renderIllustrationBadges();
            
            document.getElementById('editQType').value = 'single_choice';
            document.getElementById('editDifficulty').value = 'easy_error';
            document.getElementById('editCompulsory').value = '';
            document.getElementById('editCompulsory').onchange();
            
            document.getElementById('editQType').dispatchEvent(new Event('change'));
            document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
            clearContentOcrPreview();
            clearOcrPreview();
            
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            document.getElementById('editorSection').scrollTop = 0;
            
            backupEditorState(null, null);
        }

        function switchSidebarTab(tab) {
            activeSidebarTab = tab;

            const bankBtn = document.getElementById('sidebarTab-bank');
            const draftsBtn = document.getElementById('sidebarTab-drafts');

            if (tab === 'bank') {
                bankBtn.classList.add('active');
                draftsBtn.classList.remove('active-green');

                loadQuestions();
            } else {
                draftsBtn.classList.add('active-green');
                bankBtn.classList.remove('active');

                loadDrafts();
            }
        }

        // Toast Helper
        function switchWorkflowTab(tabId) {
            const tabs = ['ai', 'ocr', 'image'];
            tabs.forEach(t => {
                const btn = document.getElementById(`tabBtn-${t}`);
                const content = document.getElementById(`tabContent-${t}`);
                
                if (t === tabId) {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-brand-600 bg-white shadow-sm border border-slate-200";
                    content.classList.remove('hidden');
                } else {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-slate-600 hover:text-slate-800 hover:bg-white/50";
                    content.classList.add('hidden');
                }
            });
        }

        // Upload and drag and drop system for Illustration / OCR images
        function clearEditor() {
            if (confirm('确认清空当前所有的编辑草稿吗？此操作无法撤销。')) {
                cancelAllOcr(); // Cancel any active OCR requests!
                currentQuestionId = null;
                currentSeqNum = null;
                currentDraftId = null; // Reset draft id!
                document.getElementById('editorTitle').textContent = '录入新数学题';
                
                document.getElementById('editContent').value = '';
                document.getElementById('editSource').value = '';
                document.getElementById('editAnswerMarkdown').value = '';
                document.getElementById('aiCustomPrompt').value = '';
                document.getElementById('editReview').value = '';
                document.getElementById('editRelatedQuestion').value = '';
                document.getElementById('editRelatedQuestionNum').value = '';
                document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
                loadAssociatedQuestionsInList(null); // Reset associated questions panel
                
                document.getElementById('aiOutputBox').classList.add('hidden');
                document.getElementById('ocrOutputBox').classList.add('hidden');
                
                uploadedImages = [];
                renderIllustrationBadges();
                
                // Reset select lists
                document.getElementById('editQType').value = 'single_choice';
                document.getElementById('editDifficulty').value = 'easy_error';
                document.getElementById('editCompulsory').value = '';
                document.getElementById('editCompulsory').onchange();
                
                document.getElementById('editQType').dispatchEvent(new Event('change'));
                document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
                
                // Clear OCR preview
                clearContentOcrPreview();
                clearOcrPreview();
                
                // Refresh previews
                document.getElementById('editContent').dispatchEvent(new Event('input'));
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
                
                showToast('草稿已重置');

                // Reset the original state directly from the DOM!
                backupEditorState(null, null);
            }
        }

        // New question mode trigger
        // New question mode trigger
        function startNewQuestion() {
            currentQuestionId = null;
            currentSeqNum = null;
            currentDraftId = null; // Reset draft id!
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            document.getElementById('editReview').value = '';
            document.getElementById('editRelatedQuestion').value = '';
            document.getElementById('editRelatedQuestionNum').value = '';
            document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
            loadAssociatedQuestionsInList(null); // Reset associated questions panel
            
            // Clear hidden caches to avoid invisible leftovers when switching tabs
            document.getElementById('contentOcrResultText').textContent = '';
            document.getElementById('ocrResultText').textContent = '';
            document.getElementById('aiResultText').textContent = '';
            
            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            renderIllustrationBadges();
            
            // Reset selects
            document.getElementById('editQType').value = 'single_choice';
            document.getElementById('editDifficulty').value = 'easy_error';
            document.getElementById('editCompulsory').value = '';
            document.getElementById('editCompulsory').onchange();
            
            document.getElementById('editQType').dispatchEvent(new Event('change'));
            document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
            
            // Clear OCR preview
            clearContentOcrPreview();
            clearOcrPreview();
            
            // Sync-clear all previews to avoid 250ms debounce flash
            document.getElementById('contentPreview').innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
            document.getElementById('paperContent').innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将自动展示为极致精美的数学试卷排版格式。</p>';
            document.getElementById('answerPreview').innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示极其精美的 LaTeX 排版...</p>';
            document.getElementById('paperAnalysisContent').innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
            
            // Refresh previews
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            
            // Scroll editor into view
            document.getElementById('editorSection').scrollTop = 0;
            
            // Reload list styling selection
            loadQuestions();
            
            showToast('开始录入新数学题！');

            // Reset the original state directly from the DOM!
            backupEditorState(null, null);
        }

        // Load all questions to populate the related question dropdown list
        function refreshRelatedDropdown(selectedId = "") {
            fetch('/api/questions')
                .then(r => r.json())
                .then(questions => {
                    const dropdown = document.getElementById('editRelatedQuestion');
                    const numInput = document.getElementById('editRelatedQuestionNum');
                    if (!dropdown) return;
                    
                    dropdown.innerHTML = '<option value="">-- 选择要关联的题目 (可选) --</option>';
                    
                    let foundSelectedSeq = '';
                    
                    questions.forEach(q => {
                        // Exclude the current editing question
                        if (currentQuestionId && q.id === currentQuestionId) {
                            return;
                        }
                        
                        // Extract a snippet of the question stem
                        let textSnippet = q.content || '';
                        // Remove HTML/Markdown tags and LaTeX brackets to make it readable
                        textSnippet = textSnippet.replace(/[\$\#\*\_]/g, '').substring(0, 40);
                        if ((q.content || '').length > 40) textSnippet += '...';
                        
                        const optionText = `#${q.seq_num} [${getTypeText(q.question_type)}] - ${textSnippet}`;
                        const option = document.createElement('option');
                        option.value = q.id;
                        option.setAttribute('data-seq-num', q.seq_num);
                        option.textContent = optionText;
                        
                        if (String(q.id) === String(selectedId)) {
                            option.selected = true;
                            foundSelectedSeq = q.seq_num;
                        }
                        dropdown.appendChild(option);
                    });
                    
                    if (numInput) {
                        numInput.value = foundSelectedSeq;
                    }
                })
                .catch(err => {
                    console.error('Failed to load related questions list:', err);
                });
        }

        // Clear the related question association (bidirectional, backend + UI)
        function clearRelatedQuestion() {
            if (!currentQuestionId) {
                // No question loaded, just clear the UI
                const dropdown = document.getElementById('editRelatedQuestion');
                const numInput = document.getElementById('editRelatedQuestionNum');
                if (dropdown) dropdown.value = '';
                if (numInput) numInput.value = '';
                showToast('已清除关联选择', 'success');
                return;
            }

            fetch(`/api/questions/${currentQuestionId}/associated`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        // Clear UI
                        const dropdown = document.getElementById('editRelatedQuestion');
                        const numInput = document.getElementById('editRelatedQuestionNum');
                        if (dropdown) dropdown.value = '';
                        if (numInput) numInput.value = '';

                        // Hide associated list in preview
                        const wrapper = document.getElementById('paperAssociatedWrapper');
                        const container = document.getElementById('paperAssociatedList');
                        if (wrapper) wrapper.classList.add('hidden');
                        if (container) container.innerHTML = '';

                        showToast('已解除所有关联（双向生效）', 'success');
                    } else {
                        showToast(data.detail || '解除关联失败', 'error');
                    }
                })
                .catch(err => {
                    console.error('Failed to remove association:', err);
                    showToast('解除关联失败: ' + err.message, 'error');
                });
        }

        // Fetch associated questions under transitive group and populate live preview
        function loadAssociatedQuestionsInList(questionId) {
            const wrapper = document.getElementById('paperAssociatedWrapper');
            const container = document.getElementById('paperAssociatedList');
            if (wrapper) wrapper.classList.add('hidden');
            if (container) container.innerHTML = '';
            
            if (!questionId) {
                refreshRelatedDropdown("");
                return;
            }
            
            fetch(`/api/questions/${questionId}/associated`)
                .then(r => r.json())
                .then(list => {
                    let associatedId = "";
                    if (list.length > 0) {
                        associatedId = list[0].id;
                        if (wrapper) wrapper.classList.remove('hidden');
                        
                        list.forEach(q => {
                            let cleanContent = (q.content || '').replace(/[\$\#\*\_]/g, '');
                            if (cleanContent.length > 50) cleanContent = cleanContent.substring(0, 50) + '...';

                            const item = document.createElement('div');
                            item.className = "glass-list-item p-2.5 rounded-xl text-xs text-slate-700 flex items-center justify-between";
                            item.onclick = () => selectQuestionById(q.id);
                            item.innerHTML = `
                                <div class="truncate pr-2">
                                    <span class="font-bold text-brand-600 bg-brand-50 px-1.5 py-0.5 rounded text-[10px] mr-1.5 shadow-sm">#${q.seq_num}</span>
                                    <span class="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-500 mr-1.5">${getTypeText(q.question_type)}</span>
                                    <span>${cleanContent}</span>
                                </div>
                                <i class="fa-solid fa-chevron-right text-[9px] text-slate-350 shrink-0"></i>
                            `;
                            if (container) container.appendChild(item);
                        });
                    }
                    refreshRelatedDropdown(associatedId);
                })
                .catch(err => {
                    console.error('Failed to load associated questions list:', err);
                    refreshRelatedDropdown("");
                });
        }

        // Jump to select another question by ID
        function selectQuestionById(id) {
            fetch(`/api/questions/${id}`)
                .then(r => {
                    if (!r.ok) throw new Error('未找到对应的关联题目');
                    return r.json();
                })
                .then(q => {
                    checkAndSwitch(() => selectQuestion(q));
                })
                .catch(err => {
                    showToast('获取关联题目出错: ' + err.message, 'error');
                });
        }

        // Select a question to Edit & Preview
        function selectQuestion(item) {
            currentQuestionId = item.id;
            currentSeqNum = item.seq_num;
            currentDraftId = null; // Reset draft id!
            document.getElementById('editorTitle').textContent = '编辑数学题';
            
            // Clear any previous OCR preview when switching questions
            clearContentOcrPreview();
            clearOcrPreview();
            
            // Sync the active card highlight - Apple Glass Style
            const listCards = document.getElementById('questionsList').children;
            for (let i = 0; i < listCards.length; i++) {
                const card = listCards[i];
                if (parseInt(card.dataset.id) === item.id) {
                    card.classList.add('active');
                } else {
                    card.classList.remove('active');
                }
            }

            // Lazy-load details asynchronously
            fetch(`/api/questions/${item.id}`)
                .then(r => {
                    if (!r.ok) throw new Error('无法加载题目详情');
                    return r.json();
                })
                .then(fullItem => {
                    // Load values to editor
                    document.getElementById('editContent').value = fullItem.content;
                    document.getElementById('editSource').value = fullItem.source || '';
                    document.getElementById('editAnswerMarkdown').value = fullItem.answer_markdown || '';
                    document.getElementById('editReview').value = fullItem.review || '';
                    
                    uploadedImages = fullItem.image_paths || [];
                    renderIllustrationBadges();
                    
                    // Cascade bindings
                    document.getElementById('editQType').value = fullItem.question_type;
                    document.getElementById('editDifficulty').value = fullItem.difficulty;
                    
                    const compSelect = document.getElementById('editCompulsory');
                    const chapSelect = document.getElementById('editChapter');
                    const knowSelect = document.getElementById('editKnowledge');
                    
                    // In case the categories in item are not in tree yet, add them temporarily
                    if (fullItem.category_compulsory && !categoryTree[fullItem.category_compulsory]) {
                        categoryTree[fullItem.category_compulsory] = {};
                    }
                    if (fullItem.category_compulsory && fullItem.category_chapter && !categoryTree[fullItem.category_compulsory][fullItem.category_chapter]) {
                        categoryTree[fullItem.category_compulsory][fullItem.category_chapter] = [];
                    }
                    if (fullItem.category_compulsory && fullItem.category_chapter && fullItem.category_knowledge && !categoryTree[fullItem.category_compulsory][fullItem.category_chapter].includes(fullItem.category_knowledge)) {
                        categoryTree[fullItem.category_compulsory][fullItem.category_chapter].push(fullItem.category_knowledge);
                    }
                    
                    // Repopulate with values
                    populateCategoryDropdowns();
                    
                    compSelect.value = fullItem.category_compulsory || '';
                    compSelect.onchange();
                    chapSelect.value = fullItem.category_chapter || '';
                    chapSelect.onchange();
                    knowSelect.value = fullItem.category_knowledge || '';
                    
                    // Dispatch input previews or update synchronously
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
                    
                    // Load selected styled preview in paper panel
                    const badges = document.getElementById('paperBadges');
                    const sourceEl = document.getElementById('paperFooterSource');
                    
                    badges.innerHTML = `
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">编号：#${fullItem.seq_num}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-brand-50 text-brand-700">题型：${getTypeText(fullItem.question_type)}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">难度：${getDifficultyText(fullItem.difficulty)}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 inline-flex items-center"><i class="fa-regular fa-clock mr-1"></i>录入于：${formatChineseDate(fullItem.created_at)}</span>
                    `;
                    sourceEl.textContent = `来源: ${fullItem.source || '本地教研录入'}`;
                    
                    // Load associated questions list and handle group selection
                    loadAssociatedQuestionsInList(fullItem.id);
                    
                    // Scroll editor
                    document.getElementById('editorSection').scrollTop = 0;
                    
                    // Scroll to card active or highlight in current view
                    showToast(`题目 #${fullItem.seq_num} 载入成功`);
         
                    // Backup the original loaded question state directly from the DOM!
                    backupEditorState(fullItem.id, null);
                })
                .catch(err => {
                    console.error('Failed to load full question details:', err);
                    showToast('获取题目详情失败: ' + err.message, 'error');
                });
        }

        // Save/Update Question in SQLite (returns Promise)
        function saveQuestion(skipCheck = false) {
            return new Promise(async (resolve) => {
                const content = document.getElementById('editContent').value;
                const qtype = document.getElementById('editQType').value;
                const compulsory = document.getElementById('editCompulsory').value;
                const chapter = document.getElementById('editChapter').value;
                const knowledge = document.getElementById('editKnowledge').value;
                const difficulty = document.getElementById('editDifficulty').value;
                const source = document.getElementById('editSource').value;
                const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
                const review = document.getElementById('editReview').value;
                const relatedQuestionId = document.getElementById('editRelatedQuestion').value;
                
                if (!content.trim()) {
                    showToast('保存失败：题干内容不能为空！', 'error');
                    resolve(false);
                    return;
                }
                
                // Check if Compulsory or Chapter classifications are missing
                if (!skipCheck && (!compulsory || !chapter)) {
                    // If school phase (compulsory) is missing, show premium confirmation modal
                    if (!compulsory) {
                        const choice = await showMissingCompulsoryModal();
                        if (choice === 'manual') {
                            const compSelect = document.getElementById('editCompulsory');
                            compSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            // Add premium temporary focus highlight (using brand color ring)
                            compSelect.classList.remove('border-slate-200');
                            compSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                            setTimeout(() => {
                                compSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                compSelect.classList.add('border-slate-200');
                            }, 2500);
                            compSelect.focus();
                        } else if (choice === 'ai') {
                            // Automatically open AI classify modal and trigger AI analysis
                            openClassifyModal();
                            runAIClassify();
                        }
                        resolve(false);
                        return;
                    } else {
                        // If compulsory is filled but chapter is missing, fall back to focusing chapter
                        showToast('保存失败：请选择题目所属章节！', 'warning');
                        const chapSelect = document.getElementById('editChapter');
                        if (chapSelect) {
                            chapSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            chapSelect.classList.remove('border-slate-200');
                            chapSelect.classList.add('ring-2', 'ring-red-400', 'border-red-400');
                            setTimeout(() => {
                                chapSelect.classList.remove('ring-2', 'ring-red-400', 'border-red-400');
                                chapSelect.classList.add('border-slate-200');
                            }, 2500);
                            chapSelect.focus();
                        }
                        resolve(false);
                        return;
                    }
                }
                
                const formData = new FormData();
                formData.append('content', content);
                formData.append('question_type', qtype);
                formData.append('category_compulsory', compulsory);
                formData.append('category_chapter', chapter);
                formData.append('category_knowledge', knowledge);
                formData.append('difficulty', difficulty);
                formData.append('source', source);
                formData.append('answer_markdown', answerMarkdown);
                formData.append('review', review);
                formData.append('related_question_id', relatedQuestionId);
                const combinedImages = Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : [])
                ]));
                formData.append('image_paths', JSON.stringify(combinedImages));
                
                let url = '/api/questions';
                let method = 'POST';
                
                if (currentQuestionId) {
                    url = `/api/questions/${currentQuestionId}`;
                    method = 'PUT';
                }
                
                fetch(url, {
                    method: method,
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            const msg = errData.detail || errData.message || `HTTP ${r.status}`;
                            throw new Error(msg);
                        }).catch(e => {
                            if (e.message && !e.message.startsWith('HTTP')) throw e;
                            throw new Error(`服务器返回错误 HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(data => {
                    if (data.status === 'success') {
                        showToast(currentQuestionId ? '题目已成功更新！' : '题目已成功保存！');

                        // Clear OCR preview on save success
                        clearContentOcrPreview();
                        clearOcrPreview();

                        // Delete draft if it was saved from a draft
                        if (currentDraftId) {
                            let drafts = getLocalStorageDrafts();
                            drafts = drafts.filter(d => d.id !== currentDraftId);
                            setLocalStorageDrafts(drafts);
                            updateDraftCountBadge();
                            currentDraftId = null;
                        }

                        // Reload list, dropdown, and autocomplete selectors
                        loadQuestions();
                        loadCategories();
                        refreshRelatedDropdown();

                        if (!currentQuestionId) {
                            // After success insert, select it
                            selectQuestion(data.question);
                        } else {
                            // Update original state to current state directly from the DOM!
                            backupEditorState(data.question.id, null);
                        }
                        resolve(true);
                    } else {
                        showToast('保存题目失败: ' + (data.detail || data.message || '未知错误'), 'error');
                        resolve(false);
                    }
                })
                .catch(err => {
                    showToast('保存数据出错: ' + err.message, 'error');
                    resolve(false);
                });
            });
        }

        // AI classification modal handlers
        let temporaryClassifyData = null;

        function openClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            modal.classList.remove('hidden');
            
            // Reset modal states
            document.getElementById('classifyLoading').classList.add('hidden');
            document.getElementById('classifyResult').classList.add('hidden');
            document.getElementById('classifyAIButton').classList.remove('hidden');
            document.getElementById('classifyApplyButton').classList.add('hidden');
            temporaryClassifyData = null;
            
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function runAIClassify() {
            const content = document.getElementById('editContent').value;
            const loading = document.getElementById('classifyLoading');
            const resultBox = document.getElementById('classifyResult');
            const aiBtn = document.getElementById('classifyAIButton');
            const applyBtn = document.getElementById('classifyApplyButton');
            
            loading.classList.remove('hidden');
            aiBtn.classList.add('hidden');
            resultBox.classList.add('hidden');
            
            const formData = new FormData();
            formData.append('content', content);
            
            fetch('/api/ai/classify', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                loading.classList.add('hidden');
                
                if (data.status === 'success') {
                    temporaryClassifyData = data;
                    document.getElementById('recCompulsory').textContent = data.compulsory;
                    document.getElementById('recChapter').textContent = data.chapter;
                    
                    resultBox.classList.remove('hidden');
                    applyBtn.classList.remove('hidden');
                } else {
                    showToast(data.message || 'AI 智能分类分析失败！', 'error');
                    aiBtn.classList.remove('hidden');
                }
            })
            .catch(err => {
                loading.classList.add('hidden');
                aiBtn.classList.remove('hidden');
                showToast('AI 分类出错: ' + err, 'error');
            });
        }

        function applyClassifyRecommendation() {
            if (!temporaryClassifyData) return;
            
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            
            const comp = temporaryClassifyData.compulsory;
            const chap = temporaryClassifyData.chapter;
            
            // Ensure nodes exist in local dictionary structure
            if (!categoryTree[comp]) {
                categoryTree[comp] = {};
            }
            if (!categoryTree[comp][chap]) {
                categoryTree[comp][chap] = [];
            }
            
            populateCategoryDropdowns();
            
            compSelect.value = comp;
            compSelect.onchange();
            chapSelect.value = chap;
            chapSelect.onchange();
            knowSelect.value = chap; // Default empty third level (小节) to chapter name
            
            closeClassifyModal();
            showToast('AI 推荐章节已采纳！');
            
            // Save question now with skipCheck = true
            setTimeout(() => {
                saveQuestion(true);
            }, 250);
        }

        // Delete Question
        function deleteQuestion(id) {
            if (confirm('确认要在本地库中彻底删除此题目吗？不可恢复！')) {
                fetch(`/api/questions/${id}`, {
                    method: 'DELETE'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        showToast('题目已成功删除！');
                        if (currentQuestionId === id) {
                            startNewQuestion();
                        } else {
                            loadQuestions();
                            loadCategories();
                        }
                        refreshRelatedDropdown();
                    } else {
                        showToast(data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('删除题目出错: ' + err, 'error');
                });
            }
        }

        // Toggle Paper Analysis reveal
        function togglePaperAnalysis() {
            const content = document.getElementById('paperAnalysisContent');
            const icon = document.getElementById('analysisIcon');
            const text = document.getElementById('analysisText');
            
            if (content.classList.contains('hidden')) {
                content.classList.remove('hidden');
                icon.className = 'fa-solid fa-eye-slash';
                text.textContent = '隐藏解析';
            } else {
                content.classList.add('hidden');
                icon.className = 'fa-solid fa-eye';
                text.textContent = '查看解析';
            }
        }

        // ==========================================
        // LaTeX BATCH IMPORT & AI PARSE JS LOGIC
        // ==========================================
        let batchSelectedImages = [];
        let parsedQuestionsData = [];
        let allSourcesList = [];

        function openImportModal() {
            const modal = document.getElementById('latexImportModal');
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeImportModal() {
            const modal = document.getElementById('latexImportModal');
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function setupImportFileHandlers() {
            const texDrop = document.getElementById('texDropzone');
            const texInput = document.getElementById('texFileInput');
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            const latexTextarea = document.getElementById('importLatexContent');

            const imagesDrop = document.getElementById('imagesDropzone');
            const imagesInput = document.getElementById('imagesFileInput');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            const imagesListContainer = document.getElementById('importImagesList');

            // LaTeX File drag & select
            texDrop.addEventListener('click', () => texInput.click());
            texInput.addEventListener('change', (e) => handleTexFileSelect(e.target.files[0]));

            ['dragenter', 'dragover'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            texDrop.addEventListener('drop', (e) => {
                const file = e.dataTransfer.files[0];
                if (file && file.name.endsWith('.tex')) {
                    handleTexFileSelect(file);
                } else {
                    showToast('请拖入有效的 .tex 格式试卷文件！', 'warning');
                }
            });

            function handleTexFileSelect(file) {
                if (!file) return;
                texFileName.textContent = file.name;
                texFileName.className = "text-xs text-brand-600 font-bold";
                texFileIcon.className = "fa-solid fa-file-circle-check text-brand-500 text-xl mb-1.5 animate-bounce";
                
                const reader = new FileReader();
                reader.onload = (e) => {
                    latexTextarea.value = e.target.result;
                    
                    // Auto-extract title from the LaTeX file content
                    const autoTitle = extractTitleFromLatex(e.target.result);
                    if (autoTitle) {
                        const titleInput = document.getElementById('importPaperTitle');
                        titleInput.value = autoTitle;
                        showToast(`已自动从 LaTeX 文件中读取试卷标题: ${autoTitle}`);
                    }
                };
                reader.readAsText(file);
            }

            // Images File multi drag & select
            imagesDrop.addEventListener('click', () => imagesInput.click());
            imagesInput.addEventListener('change', (e) => handleImagesSelect(e.target.files));

            ['dragenter', 'dragover'].forEach(eventName => {
                imagesDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    imagesDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                imagesDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    imagesDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            imagesDrop.addEventListener('drop', (e) => {
                handleImagesSelect(e.dataTransfer.files);
            });

            function handleImagesSelect(files) {
                if (!files || files.length === 0) return;
                for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    if (f.type.startsWith('image/')) {
                        if (!batchSelectedImages.some(img => img.name === f.name)) {
                            batchSelectedImages.push(f);
                        }
                    }
                }
                renderImagesList();
            }

            function renderImagesList() {
                imagesListContainer.innerHTML = '';
                if (batchSelectedImages.length === 0) {
                    imagesListContainer.classList.add('hidden');
                    imagesCountName.textContent = "点击或多选拖入试卷引用的所有图片";
                    imagesCountName.className = "text-xs text-slate-600 font-medium";
                    imagesFileIcon.className = "fa-solid fa-images text-slate-400 text-xl mb-1.5";
                    return;
                }

                imagesListContainer.classList.remove('hidden');
                imagesCountName.textContent = `已选择 ${batchSelectedImages.length} 张图片`;
                imagesCountName.className = "text-xs text-brand-600 font-bold";
                imagesFileIcon.className = "fa-solid fa-images text-brand-500 text-xl mb-1.5 animate-pulse";

                batchSelectedImages.forEach((file, index) => {
                    const item = document.createElement('div');
                    item.className = "relative group flex items-center justify-between bg-white border border-slate-200 rounded-lg px-2 py-0.5 text-[10px] text-slate-600 space-x-1.5 shrink-0 max-w-[140px]";
                    item.innerHTML = `
                        <span class="truncate font-semibold max-w-[90px]" title="${file.name}">${file.name}</span>
                        <button class="text-slate-400 hover:text-red-500 transition-colors" title="移除">
                            <i class="fa-solid fa-circle-xmark"></i>
                        </button>
                    `;
                    item.querySelector('button').addEventListener('click', (e) => {
                        e.stopPropagation();
                        batchSelectedImages.splice(index, 1);
                        renderImagesList();
                    });
                    imagesListContainer.appendChild(item);
                });
            }

            // Input event listener for pasted LaTeX or manual edits
            latexTextarea.addEventListener('input', () => {
                const autoTitle = extractTitleFromLatex(latexTextarea.value);
                if (autoTitle) {
                    const titleInput = document.getElementById('importPaperTitle');
                    if (titleInput.value.trim() === '') {
                        titleInput.value = autoTitle;
                        showToast(`已从输入中自动读取试卷标题: ${autoTitle}`);
                    }
                }
            });
        }

        function appendImportLog(message, type = 'info') {
            const consoleDiv = document.getElementById('importLogsConsole');
            if (!consoleDiv) return;
            const logEl = document.createElement('div');
            
            let colorClass = 'text-brand-400';
            if (type === 'success') colorClass = 'text-green-400';
            if (type === 'error') colorClass = 'text-red-400';
            if (type === 'warning') colorClass = 'text-yellow-400';
            
            logEl.className = `${colorClass} py-0.5`;
            logEl.innerHTML = `[${new Date().toLocaleTimeString()}] ${message}`;
            consoleDiv.appendChild(logEl);
            consoleDiv.scrollTop = consoleDiv.scrollHeight;
        }

        function runAIPaperParse() {
            const titleInput = document.getElementById('importPaperTitle');
            const title = titleInput.value.trim();
            const latex = document.getElementById('importLatexContent').value.trim();

            if (!latex) {
                showToast('请粘贴或上传 LaTeX 试卷内容！', 'warning');
                return;
            }

            if (!title) {
                if (!confirm('试卷标题为空，导入后题目来源将显示为空。\n确定继续吗？')) {
                    titleInput.focus();
                    return;
                }
            }

            // Hide placeholder & results, show loading skeleton
            document.getElementById('importPlaceholder').classList.add('hidden');
            document.getElementById('parsedQuestionsWrapper').classList.add('hidden');
            document.getElementById('importLoadingState').classList.remove('hidden');
            
            // Clear logs
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 初始化 AI 拆解任务...</div>';
            
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = true;
            runBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>正在全力拆解中...</span>';

            document.getElementById('importLoadingText').textContent = '正在上传配套图片并整理文件名映射...';
            appendImportLog('开始检查配套图片...', 'info');

            let uploadPromise = Promise.resolve({});
            if (batchSelectedImages.length > 0) {
                appendImportLog(`检测到 ${batchSelectedImages.length} 张配图，开始多线程上传中...`, 'info');
                const imgFormData = new FormData();
                batchSelectedImages.forEach(file => {
                    imgFormData.append('files', file);
                });

                uploadPromise = fetch('/api/upload/batch', {
                    method: 'POST',
                    body: imgFormData
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        appendImportLog('批量配图上传成功！已成功建立本地重命名路径映射。', 'success');
                        return data.mapping;
                    } else {
                        throw new Error(data.message || '图片上传失败');
                    }
                });
            } else {
                appendImportLog('无插图需关联，直接运行大文本 AI 拆解。', 'info');
            }

            uploadPromise
                .then(imageMapping => {
                    let parseModelFriendly = 'DeepSeek-V4-Flash';
                    let parseBrand = 'DeepSeek';
                    if (systemPreferParseModel === 'qwen3.7-max' || systemPreferParseModel.includes('qwen')) {
                        parseModelFriendly = 'Ali Bailian Qwen3.7-Max';
                        parseBrand = '阿里百炼 Qwen';
                    } else if (systemPreferParseModel === 'deepseek-v4-pro') {
                        parseModelFriendly = 'DeepSeek-V4-Pro';
                    }
                    
                    document.getElementById('importLoadingText').textContent = `${parseBrand} 正在极速拆解试卷 (预计仅需 5-25 秒)...`;
                    appendImportLog(`正在调用 ${parseModelFriendly} 教研大模型进行试题智能分割与属性匹配...`, 'info');
                    appendImportLog('大纲映射范围：高中人教版A 必修一至选择性必修三。请耐心等候...', 'info');

                    const generateAnswersCheckbox = document.getElementById('importGenerateAnswers');
                    const generateAnswers = generateAnswersCheckbox ? generateAnswersCheckbox.checked : false;

                    const parseFormData = new FormData();
                    parseFormData.append('latex_content', latex);
                    parseFormData.append('paper_title', title);
                    parseFormData.append('image_mapping_json', JSON.stringify(imageMapping));
                    parseFormData.append('generate_answers', generateAnswers ? "true" : "false");

                    return fetch('/api/ai/parse-paper', {
                        method: 'POST',
                        body: parseFormData
                    });
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(data => {
                    if (data.status === 'success') {
                        parsedQuestionsData = data.questions;
                        appendImportLog(`试卷成功拆解完成！共提取出 ${parsedQuestionsData.length} 道高定数学题。`, 'success');
                        
                        renderParsedQuestionsList(parsedQuestionsData);
                        
                        document.getElementById('importLoadingState').classList.add('hidden');
                        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
                    } else {
                        throw new Error(data.message || '拆解失败');
                    }
                })
                .catch(err => {
                    console.error(err);
                    appendImportLog(`拆解出错: ${err.message}`, 'error');

                    // 更新加载状态为中断样式
                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = '试卷拆解中断！';

                    // 添加重置按钮
                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');

                    showToast(`试卷拆解失败: ${err.message}`, 'error');
                })
                .finally(() => {
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
        }

        function clearAllImportInputs() {
            // 清空左侧输入栏
            const titleInput = document.getElementById('importPaperTitle');
            if (titleInput) titleInput.value = '';

            const latexTextarea = document.getElementById('importLatexContent');
            if (latexTextarea) latexTextarea.value = '';

            const texFileInput = document.getElementById('texFileInput');
            if (texFileInput) texFileInput.value = '';

            const imagesFileInput = document.getElementById('imagesFileInput');
            if (imagesFileInput) imagesFileInput.value = '';

            // 重置 .tex 拖拽显示样式
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            if (texFileName) {
                texFileName.textContent = "点击或拖放拖入 .tex 格式试卷文件";
                texFileName.className = "text-xs text-slate-600 font-medium";
            }
            if (texFileIcon) {
                texFileIcon.className = "fa-solid fa-file-code text-slate-400 text-xl mb-1.5";
            }

            // 清空批量配图
            batchSelectedImages = [];
            // 重置图片展示列表与状态
            renderImagesList();
        }

        function resetImportState(showToastMessage = true) {
            // 隐藏加载状态和结果视图
            document.getElementById('importLoadingState').classList.add('hidden');
            document.getElementById('parsedQuestionsWrapper').classList.add('hidden');

            // 显示占位视图
            document.getElementById('importPlaceholder').classList.remove('hidden');

            // 恢复加载状态的原始图标
            const loadingIcon = document.querySelector('#importLoadingState .fa-spinner, #importLoadingState .fa-circle-exclamation');
            if (loadingIcon) {
                loadingIcon.classList.remove('fa-circle-exclamation', 'text-red-500');
                loadingIcon.classList.add('fa-spinner', 'animate-spin');
            }

            // 重置加载文本
            document.getElementById('importLoadingText').textContent = '正在整理插图映射并预备上传...';

            // 隐藏重置按钮
            const resetBtn = document.getElementById('resetImportBtn');
            if (resetBtn) {
                resetBtn.classList.add('hidden');
            }

            // 清空日志控制台
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 准备就绪，等待上传图片...</div>';

            // 重置按钮状态
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = false;
            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';

            // 清空解析结果数据
            parsedQuestionsData = [];
            if (typeof updateSelectedCount === 'function') {
                updateSelectedCount();
            }

            const shouldShow = (showToastMessage === true || typeof showToastMessage !== 'boolean');
            if (shouldShow) {
                showToast('已重置，可以重新开始拆解', 'success');
            }
        }


        function renderParsedQuestionsList(questions) {
            const container = document.getElementById('parsedCardsContainer');
            container.innerHTML = '';
            document.getElementById('parsedCountBadge').textContent = `共 ${questions.length} 题`;

            if (questions.length === 0) {
                container.innerHTML = '<div class="p-12 text-center text-slate-400 text-xs">AI 未能拆解出任何有效的题目，请检查 LaTeX 格式是否规整。</div>';
                if (typeof updateSelectedCount === 'function') updateSelectedCount();
                return;
            }

            questions.forEach((q, index) => {
                const card = document.createElement('div');
                card.className = "glass-card rounded-xl p-4 space-y-3 flex flex-col relative";
                card.id = `parsed-card-${index}`;
                
                card.innerHTML = `
                    <!-- Card Top Configs Bar -->
                    <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 border-b pb-3 shrink-0">
                        <div class="flex items-center space-x-2 select-none text-slate-700 text-xs font-bold">
                            <input type="checkbox" data-index="${index}" class="card-select-checkbox h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500 cursor-pointer transition-colors" ${q.saved ? 'disabled opacity-50' : 'checked'} onclick="event.stopPropagation()">
                            <span class="h-5 w-5 bg-brand-50 text-brand-600 rounded-full flex items-center justify-center text-[10px] font-bold border border-brand-100">${index + 1}</span>
                            <span>题型与难度</span>
                        </div>
                        <select class="card-qtype glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            <option value="single_choice" ${q.question_type === 'single_choice' ? 'selected' : ''}>单选题</option>
                            <option value="multi_choice" ${q.question_type === 'multi_choice' ? 'selected' : ''}>多选题</option>
                            <option value="fill_in_blank" ${q.question_type === 'fill_in_blank' ? 'selected' : ''}>填空题</option>
                            <option value="detailed_answer" ${q.question_type === 'detailed_answer' ? 'selected' : ''}>解答题</option>
                        </select>
                        <select class="card-difficulty glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            <option value="easy_error" ${q.difficulty === 'easy_error' ? 'selected' : ''}>易错题</option>
                            <option value="challenge" ${q.difficulty === 'challenge' ? 'selected' : ''}>挑战题</option>
                            <option value="qiangji" ${q.difficulty === 'qiangji' ? 'selected' : ''}>强基题</option>
                        </select>
                        <input type="text" class="card-source glass-input px-2.5 py-1.5 rounded-lg text-2xs font-semibold" value="${q.source || ''}" placeholder="题目来源">
                        <!-- Success / Saved indicator -->
                        <div class="flex items-center justify-end">
                            <span class="card-status-badge text-[10px] font-bold px-2 py-0.5 rounded ${q.saved ? 'bg-green-50 text-green-700 border border-green-150' : 'bg-slate-100 text-slate-500'}">${q.saved ? '已导入' : '待导入'}</span>
                        </div>
                    </div>

                    <!-- Curriculum linkage section -->
                    <div class="grid grid-cols-3 gap-2 border-b pb-3 shrink-0">
                        <select class="card-compulsory glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            <option value="">所有学段</option>
                        </select>
                        <select class="card-chapter glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            <option value="">所有章节</option>
                        </select>
                        <select class="card-knowledge glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            <option value="">所有小节</option>
                        </select>
                    </div>

                    <!-- Body Content Split -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
                        <!-- Left Side: Inputs -->
                        <div class="space-y-2 flex flex-col justify-start">
                            <div class="space-y-1">
                                <label class="text-[9px] font-bold text-slate-450 tracking-wider">题干编辑</label>
                                <textarea class="card-content-textarea glass-input w-full h-24 p-2.5 rounded-lg font-mono text-2xs resize-none custom-scrollbar">${q.content || ''}</textarea>
                            </div>
                            <div class="space-y-1">
                                <label class="text-[9px] font-bold text-slate-450 tracking-wider">答案与解析编辑</label>
                                <textarea class="card-answer-textarea glass-input w-full h-24 p-2.5 rounded-lg font-mono text-2xs resize-none custom-scrollbar">${q.answer_markdown || ''}</textarea>
                            </div>
                        </div>

                        <!-- Right Side: Realtime KaTeX Previews -->
                        <div class="border border-slate-150 rounded-xl bg-slate-50/60 p-3 overflow-y-auto max-h-56 space-y-2.5 text-xs font-serif leading-relaxed custom-scrollbar flex flex-col justify-start relative select-text">
                            <span class="absolute top-2 right-2 text-[8px] font-bold text-slate-400 bg-white/80 px-1.5 py-0.5 rounded border tracking-wider select-none">实时渲染</span>
                            <div class="card-content-preview border-b border-slate-200/60 pb-2 text-slate-800"></div>
                            <div class="card-answer-preview text-slate-650"></div>
                        </div>
                    </div>

                    <!-- Card Actions Footer -->
                    <div class="flex justify-between items-center border-t border-slate-100 pt-3 shrink-0">
                        <div class="flex flex-wrap gap-1.5 items-center max-w-[70%]" id="card-images-badges-${index}">
                            <!-- Thumbnail labels of images selected -->
                        </div>
                        <button onclick="saveParsedQuestion(${index})" class="card-save-btn px-4 py-1.5 rounded-lg text-2xs flex items-center space-x-1 shrink-0 ${q.saved ? 'bg-green-50 text-green-700 font-bold border border-green-200 cursor-not-allowed' : 'glass-btn text-brand-700 font-bold'}" ${q.saved ? 'disabled' : ''}>
                            <i class="fa-solid ${q.saved ? 'fa-check' : 'fa-file-arrow-up'}"></i>
                            <span>${q.saved ? '已导入' : '导入此题'}</span>
                        </button>
                    </div>
                `;

                container.appendChild(card);
                setupCardCategoryLinkage(card, q);

                // Populate image badges
                const badgesContainer = document.getElementById(`card-images-badges-${index}`);
                const mappedImgs = q.image_paths || [];
                mappedImgs.forEach(path => {
                    const filename = path.split('/').pop();
                    const badge = document.createElement('div');
                    badge.className = "flex items-center space-x-1 px-2 py-0.5 bg-slate-100 border rounded-full text-[9px] font-semibold text-slate-500 hover:bg-white transition-colors cursor-pointer select-none";
                    badge.innerHTML = `<i class="fa-solid fa-image text-slate-400"></i><span class="truncate max-w-[80px]" title="${filename}">${filename}</span>`;
                    badgesContainer.appendChild(badge);
                });

                // Set up checkbox listener
                const selectCb = card.querySelector('.card-select-checkbox');
                selectCb.addEventListener('change', () => {
                    if (typeof updateSelectedCount === 'function') updateSelectedCount();
                });

                // Set up preview
                const textInput = card.querySelector('.card-content-textarea');
                const ansInput = card.querySelector('.card-answer-textarea');
                
                const triggerPreview = () => {
                    renderParsedCardPreview(card, textInput.value, ansInput.value);
                };

                textInput.addEventListener('input', debounce(triggerPreview, 200));
                ansInput.addEventListener('input', debounce(triggerPreview, 200));

                triggerPreview();
            });

            if (typeof updateSelectedCount === 'function') updateSelectedCount();
        }

        function setupCardCategoryLinkage(card, q) {
            const compSelect = card.querySelector('.card-compulsory');
            const chapSelect = card.querySelector('.card-chapter');
            const knowSelect = card.querySelector('.card-knowledge');

            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            Object.keys(categoryTree).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                if (c === q.category_compulsory) opt.selected = true;
                compSelect.appendChild(opt);
            });

            const updateChapters = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;

                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        const opt = document.createElement('option');
                        opt.value = ch;
                        opt.textContent = ch;
                        if (ch === q.category_chapter) opt.selected = true;
                        chapSelect.appendChild(opt);
                    });
                } else {
                    chapSelect.disabled = true;
                }
            };

            const updateKnowledge = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (默认整章) --</option>';

                if (comp && chap && categoryTree[comp][chap]) {
                    knowSelect.disabled = false;
                    categoryTree[comp][chap].forEach(k => {
                        const opt = document.createElement('option');
                        opt.value = k;
                        opt.textContent = k;
                        if (k === q.category_knowledge) opt.selected = true;
                        knowSelect.appendChild(opt);
                    });
                } else {
                    knowSelect.disabled = true;
                }
            };

            compSelect.addEventListener('change', () => {
                updateChapters();
                updateKnowledge();
            });

            chapSelect.addEventListener('change', () => {
                updateKnowledge();
            });

            updateChapters();
            updateKnowledge();
        }

        function renderParsedCardPreview(card, contentText, answerText) {
            const contentPrev = card.querySelector('.card-content-preview');
            const answerPrev = card.querySelector('.card-answer-preview');
            
            // For content
            if (!contentText.trim()) {
                contentPrev.innerHTML = '<span class="text-slate-400 italic text-2xs">题干预览将在此实时渲染...</span>';
            } else {
                try {
                    contentPrev.innerHTML = parseMarkdownWithMath(contentText);
                    renderMathInElement(contentPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    contentPrev.textContent = contentText;
                }
            }

            // For answer
            if (!answerText.trim()) {
                answerPrev.innerHTML = '<span class="text-slate-400 italic text-2xs">解析预览将在此实时渲染...</span>';
            } else {
                try {
                    answerPrev.innerHTML = parseMarkdownWithMath(answerText);
                    renderMathInElement(answerPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    answerPrev.textContent = answerText;
                }
            }
        }

        function saveParsedQuestion(index) {
            const q = parsedQuestionsData[index];
            if (!q || q.saved) return Promise.resolve(true);

            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return Promise.reject(new Error('Card element not found'));

            const content = card.querySelector('.card-content-textarea').value.trim();
            const answer_markdown = card.querySelector('.card-answer-textarea').value.trim();
            const question_type = card.querySelector('.card-qtype').value;
            const difficulty = card.querySelector('.card-difficulty').value;
            const source = card.querySelector('.card-source').value.trim();
            
            const category_compulsory = card.querySelector('.card-compulsory').value;
            const category_chapter = card.querySelector('.card-chapter').value;
            const category_knowledge = card.querySelector('.card-knowledge').value;

            if (!content) {
                showToast(`第 ${index + 1} 题的题干内容不能为空！`, 'warning');
                return Promise.reject(new Error('Content empty'));
            }
            if (!category_compulsory || !category_chapter) {
                showToast(`请选择第 ${index + 1} 题的学段与所属章节！`, 'warning');
                
                // Auto-scroll to the missing classification select inside this specific parsed card!
                const compSelect = card.querySelector('.card-compulsory');
                const chapSelect = card.querySelector('.card-chapter');
                const targetSelect = !category_compulsory ? compSelect : chapSelect;
                
                if (targetSelect) {
                    targetSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    targetSelect.classList.remove('border-slate-200');
                    targetSelect.classList.add('ring-2', 'ring-red-400', 'border-red-400');
                    setTimeout(() => {
                        targetSelect.classList.remove('ring-2', 'ring-red-400', 'border-red-400');
                        targetSelect.classList.add('border-slate-200');
                    }, 2500);
                    targetSelect.focus();
                }
                
                return Promise.reject(new Error('Curriculum empty'));
            }

            const saveBtn = card.querySelector('.card-save-btn');
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>保存中...</span>';

            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', question_type);
            formData.append('category_compulsory', category_compulsory);
            formData.append('category_chapter', category_chapter);
            formData.append('category_knowledge', category_knowledge);
            formData.append('difficulty', difficulty);
            formData.append('source', source);
            formData.append('answer_markdown', answer_markdown);
            formData.append('image_paths', JSON.stringify(q.image_paths || []));

            return fetch('/api/questions', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    q.saved = true;
                    
                    const statusBadge = card.querySelector('.card-status-badge');
                    statusBadge.textContent = '已导入';
                    statusBadge.className = 'card-status-badge text-[10px] font-bold px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-150 animate-pulse';
                    
                    saveBtn.className = 'card-save-btn px-4 py-1.5 rounded-lg bg-green-50 text-green-700 font-bold text-2xs border border-green-200 cursor-not-allowed';
                    saveBtn.innerHTML = '<i class="fa-solid fa-check"></i> <span>已导入</span>';
                    
                    const cb = card.querySelector('.card-select-checkbox');
                    if (cb) {
                        cb.disabled = true;
                        cb.checked = false;
                        cb.classList.add('opacity-50');
                    }
                    if (typeof updateSelectedCount === 'function') {
                        updateSelectedCount();
                    }

                    showToast(`第 ${index + 1} 题导入成功！`);
                    
                    loadCategories();
                    loadQuestions();
                    return true;
                } else {
                    throw new Error(data.message || '保存失败');
                }
            })
            .catch(err => {
                showToast(`第 ${index + 1} 题保存出错: ${err.message}`, 'error');
                saveBtn.disabled = false;
                saveBtn.innerHTML = '<i class="fa-solid fa-file-arrow-up"></i> <span>导入此题</span>';
                throw err;
            });
        }

        function clearAllParsedSources() {
            const inputs = document.querySelectorAll('#parsedCardsContainer .card-source');
            if (inputs.length === 0) {
                showToast('当前拆解列表为空！', 'warning');
                return;
            }
            inputs.forEach(input => {
                input.value = '';
            });
            showToast('已成功一键清空所有拆解题目的试卷标题来源！', 'success');
        }

        function getCheckedUnsavedIndices() {
            const indices = [];
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved && cb.checked) {
                    indices.push(idx);
                }
            });
            return indices;
        }

        function updateSelectedCount() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            let unsavedCount = 0;
            let checkedCount = 0;
            
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved) {
                    unsavedCount++;
                    if (cb.checked) {
                        checkedCount++;
                    }
                }
            });
            
            const badge = document.getElementById('selectedCountBadge');
            if (badge) {
                badge.textContent = `已选 ${checkedCount} / ${unsavedCount} 题`;
            }
            
            const selectAllCb = document.getElementById('selectAllCheckbox');
            if (selectAllCb) {
                if (unsavedCount === 0) {
                    selectAllCb.checked = false;
                    selectAllCb.indeterminate = false;
                    selectAllCb.disabled = true;
                } else {
                    selectAllCb.disabled = false;
                    if (checkedCount === unsavedCount) {
                        selectAllCb.checked = true;
                        selectAllCb.indeterminate = false;
                    } else if (checkedCount === 0) {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = false;
                    } else {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = true;
                    }
                }
            }
            
            const btnText = document.getElementById('saveAllParsedBtnText');
            if (btnText) {
                btnText.textContent = checkedCount > 0 ? `导入选中 (${checkedCount})` : `导入选中题目`;
            }

            const saveAllBtn = document.getElementById('saveAllParsedBtn');
            if (saveAllBtn) {
                if (checkedCount === 0) {
                    saveAllBtn.disabled = true;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-slate-100 text-slate-400 font-bold text-xs border border-slate-200 shadow-sm cursor-not-allowed transition-all";
                } else {
                    saveAllBtn.disabled = false;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-brand-600/80 hover:bg-brand-600 text-white font-bold text-xs backdrop-blur-sm border border-brand-500/20 shadow-sm transition-all active:scale-95 cursor-pointer";
                }
            }
        }

        function toggleSelectAllParsed(checked) {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (!cb.disabled) {
                    cb.checked = checked;
                }
            });
            updateSelectedCount();
        }

        function invertSelectParsed() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (!cb.disabled) {
                    cb.checked = !cb.checked;
                }
            });
            updateSelectedCount();
        }

        function saveAllParsedQuestions() {
            const selectedIndices = getCheckedUnsavedIndices();

            if (selectedIndices.length === 0) {
                const unsavedCount = parsedQuestionsData.filter(q => !q.saved).length;
                if (unsavedCount === 0) {
                    showToast('所有题目已成功导入！', 'info');
                } else {
                    showToast('请先勾选需要导入的题目！', 'warning');
                }
                return;
            }

            const mainBtn = document.getElementById('saveAllParsedBtn');
            if (!mainBtn) return;
            
            mainBtn.disabled = true;
            
            const btnText = document.getElementById('saveAllParsedBtnText');
            const originalText = btnText ? btnText.textContent : '导入选中题目';
            if (btnText) {
                btnText.textContent = '批量入库中...';
            }
            
            const icon = mainBtn.querySelector('i');
            const originalIconClass = icon ? icon.className : 'fa-solid fa-cloud-arrow-up';
            if (icon) {
                icon.className = 'fa-solid fa-spinner animate-spin';
            }

            showToast(`正在批量导入 ${selectedIndices.length} 道勾选题目，请稍候...`);

            const promises = selectedIndices.map(idx => saveParsedQuestion(idx).catch(() => null));

            Promise.all(promises)
                .then(results => {
                    const successCount = results.filter(r => r === true).length;
                    
                    updateSelectedCount();
                    const remainingUnsavedCount = parsedQuestionsData.filter(q => !q.saved).length;
                    
                    if (remainingUnsavedCount === 0) {
                        showToast(`批量导入完成！共 ${successCount} 道题目已全部成功导入本地库！`, 'success');
                        
                        setTimeout(() => {
                            clearAllImportInputs();
                            resetImportState(false); 
                            closeImportModal();      
                        }, 1500);
                    } else {
                        showToast(`批量导入已完成！成功: ${successCount}/${selectedIndices.length}。剩余未导入的题目已保留，请确认。`, 'warning');
                    }
                })
                .catch(err => {
                    showToast(`批量导入时发生严重错误: ${err.message}`, 'error');
                })
                .finally(() => {
                    mainBtn.disabled = false;
                    if (icon) {
                        icon.className = originalIconClass;
                    }
                    if (btnText) {
                        btnText.textContent = originalText;
                    }
                    updateSelectedCount();
                });
        }


        // ==========================================
        // SIDEBAR QUESTION SOURCE AUTOCOMPLETE FILTER
        // ==========================================
        function setupSourceFilterAutocomplete() {
            const sourceInput = document.getElementById('filterSource');
            const suggestionsDiv = document.getElementById('filterSourceSuggestions');
            const toggleBtn = document.getElementById('toggleFilterSourceBtn');
            const clearBtn = document.getElementById('clearFilterSourceBtn');
            const chevronIcon = document.getElementById('chevronFilterSourceIcon');
            
            if (!sourceInput || !suggestionsDiv) return;

            function fetchSources(callback) {
                fetch('/api/sources')
                    .then(r => r.json())
                    .then(sources => {
                        allSourcesList = sources;
                        if (callback) callback(sources);
                    })
                    .catch(err => {
                        console.error('Failed to fetch sources:', err);
                    });
            }
            
            function renderSuggestions(list) {
                suggestionsDiv.innerHTML = '';
                if (list.length === 0) {
                    suggestionsDiv.innerHTML = '<div class="px-3 py-2 text-2xs text-slate-400 italic text-center select-none">无匹配来源</div>';
                    suggestionsDiv.classList.remove('hidden');
                    chevronIcon.classList.add('rotate-180');
                    return;
                }

                list.forEach(src => {
                    const item = document.createElement('div');
                    item.className = "px-3 py-2 hover:bg-slate-50 text-xs text-slate-700 cursor-pointer select-none truncate font-medium transition-colors border-b border-slate-100/50 last:border-b-0";
                    item.textContent = src;
                    item.addEventListener('click', () => {
                        sourceInput.value = src;
                        suggestionsDiv.classList.add('hidden');
                        chevronIcon.classList.remove('rotate-180');
                        updateClearButtonVisibility();
                        currentBankPage = 1;
                        currentDraftPage = 1;
                        if (activeSidebarTab === 'bank') {
                            loadQuestions();
                        } else {
                            loadDrafts();
                        }
                    });
                    suggestionsDiv.appendChild(item);
                });
                suggestionsDiv.classList.remove('hidden');
                chevronIcon.classList.add('rotate-180');
            }
            
            function updateClearButtonVisibility() {
                if (sourceInput.value.trim() !== '') {
                    clearBtn.classList.remove('hidden');
                } else {
                    clearBtn.classList.add('hidden');
                }
            }
            
            sourceInput.addEventListener('focus', () => {
                fetchSources(sources => {
                    const val = sourceInput.value.trim().toLowerCase();
                    if (val === '') {
                        renderSuggestions(sources);
                    } else {
                        const filtered = sources.filter(s => s.toLowerCase().includes(val));
                        renderSuggestions(filtered);
                    }
                });
            });
            
            sourceInput.addEventListener('input', () => {
                updateClearButtonVisibility();
                const val = sourceInput.value.trim().toLowerCase();
                if (val === '') {
                    renderSuggestions(allSourcesList);
                } else {
                    const filtered = allSourcesList.filter(s => s.toLowerCase().includes(val));
                    renderSuggestions(filtered);
                }
            });
            
            sourceInput.addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });
            
            sourceInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                    currentBankPage = 1;
                    currentDraftPage = 1;
                    if (activeSidebarTab === 'bank') {
                        loadQuestions();
                    } else {
                        loadDrafts();
                    }
                }
            });
            
            toggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!suggestionsDiv.classList.contains('hidden')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                } else {
                    sourceInput.focus();
                }
            });
            
            clearBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sourceInput.value = '';
                updateClearButtonVisibility();
                suggestionsDiv.classList.add('hidden');
                chevronIcon.classList.remove('rotate-180');
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });
            
            document.addEventListener('click', (e) => {
                if (!e.target.closest('#filterSourceContainer')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                }
            });
        }

        // ==========================================
        // LaTeX TITLE AUTO-EXTRACTION HELPERS
        // ==========================================
        function extractTitleFromLatex(latex) {
            if (!latex) return "";
            
            // 1. Try \title{...}
            let match = latex.match(/\\title\s*\{([^}]+)\}/);
            if (match && match[1]) {
                return cleanLatexFormatting(match[1]);
            }
            
            // 2. Try \chead{...}
            match = latex.match(/\\chead\s*\{([^}]+)\}/);
            if (match && match[1]) {
                const clean = cleanLatexFormatting(match[1]);
                if (!clean.includes("页") && !clean.includes("绝密")) {
                    return clean;
                }
            }
            
            // 3. Try \begin{center} ... \end{center} near the top
            const topPart = latex.slice(0, 1500);
            match = topPart.match(/\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}/);
            if (match && match[1]) {
                let content = match[1].trim();
                content = content.replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '');
                content = content.replace(/\\textbf\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\heiti\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\kt\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/[\{\}]/g, '');
                
                const lines = content.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('%') && !l.includes('\\includegraphics') && !l.includes('\\chead') && !l.includes('\\lhead'));
                if (lines.length > 0) {
                    for (let line of lines) {
                        line = cleanLatexFormatting(line);
                        if (line.includes("中学") || line.includes("试卷") || line.includes("试题") || line.includes("考试") || line.includes("期") || line.includes("测试") || line.includes("年")) {
                            return line;
                        }
                    }
                    return cleanLatexFormatting(lines[0]);
                }
            }
            
            // 4. Try \lhead or \rhead headers
            match = latex.match(/\\(lhead|rhead)\s*\{([^}]+)\}/);
            if (match && match[2]) {
                const clean = cleanLatexFormatting(match[2]);
                if (clean.includes("中学") || clean.includes("试卷") || clean.includes("试题") || clean.includes("考试")) {
                    return clean;
                }
            }
            
            return "";
        }

        function cleanLatexFormatting(str) {
            if (!str) return "";
            return str
                .replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '')
                .replace(/\\textbf\s*\{([^}]+)\}/g, '$1')
                .replace(/\\heiti\s*\{([^}]+)\}/g, '$1')
                .replace(/\\kt\s*\{([^}]+)\}/g, '$1')
                .replace(/\\sffamily/g, '')
                .replace(/\\centering/g, '')
                .replace(/[\{\}]/g, '')
                .replace(/\\\\/g, '')
                .trim();
        }

        // App Initialization
        document.addEventListener('DOMContentLoaded', () => {
            // Check configs
            fetchConfigStatus();
            
            // Load and update drafts count
            updateDraftCountBadge();

            // Bind search input to loadQuestions / loadDrafts dynamically
            document.getElementById('searchInput').addEventListener('input', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            // Bind filterType and filterDifficulty select elements dynamically to loadQuestions / loadDrafts
            document.getElementById('filterType').addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            document.getElementById('filterDifficulty').addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            // Bind filterSort change event
            const filterSortEl = document.getElementById('filterSort');
            if (filterSortEl) {
                filterSortEl.addEventListener('change', () => {
                    currentBankPage = 1;
                    currentDraftPage = 1;
                    if (activeSidebarTab === 'bank') {
                        loadQuestions();
                    } else {
                        loadDrafts();
                    }
                });
            }

            // Load Cascade Category Tree
            loadCategories();
            
            // Load saved questions list
            loadQuestions();
            
            // Load and populate related questions dropdown
            refreshRelatedDropdown();

            // Set up related question display number input two-way synchronization
            const relatedNumInput = document.getElementById('editRelatedQuestionNum');
            const relatedSelect = document.getElementById('editRelatedQuestion');
            if (relatedNumInput && relatedSelect) {
                relatedNumInput.addEventListener('input', () => {
                    const val = relatedNumInput.value.trim();
                    if (!val) {
                        relatedSelect.value = '';
                    } else {
                        let found = false;
                        for (let i = 0; i < relatedSelect.options.length; i++) {
                            const opt = relatedSelect.options[i];
                            if (opt.getAttribute('data-seq-num') === val) {
                                relatedSelect.value = opt.value;
                                found = true;
                                break;
                            }
                        }
                        if (!found) {
                            relatedSelect.value = '';
                        }
                    }
                });

                relatedSelect.addEventListener('change', () => {
                    const selectedOpt = relatedSelect.options[relatedSelect.selectedIndex];
                    if (selectedOpt && selectedOpt.value) {
                        relatedNumInput.value = selectedOpt.getAttribute('data-seq-num') || '';
                    } else {
                        relatedNumInput.value = '';
                    }
                });
            }

            // Set up debounced event listeners for realtime markdown preview
            setupRealtimePreviews();

            // Setup drag-and-drop & clipboard listeners for illustrations & OCR
            setupUploadHandlers();
            
            // Setup resizers
            initResizers();

            // Setup searchable source filter autocomplete
            setupSourceFilterAutocomplete();

            // Setup LaTeX batch import handlers
            setupImportFileHandlers();

            // Initialize empty original state
            backupEditorState(null, null);
        });

