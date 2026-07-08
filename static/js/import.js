        function startNewQuestionWithoutPrompt() {
            currentQuestionId = null;
            currentDraftId = null;
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            
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
                if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
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

        function startNewQuestion() {
            currentQuestionId = null;
            currentSeqNum = null;
            currentDraftId = null; // Reset draft id!
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            window.lastOcrOriginalImagePath = '';
            window.contentLastCompiledTikzPath = '';
            window.answerLastCompiledTikzPath = '';
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            document.getElementById('editReview').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            if (document.getElementById('editContentTikzCode')) document.getElementById('editContentTikzCode').value = '';
            if (document.getElementById('editAnswerTikzCode')) document.getElementById('editAnswerTikzCode').value = '';
            
            // Hide Content & Answer TikZ Panels
            if (document.getElementById('contentTikzContainer')) document.getElementById('contentTikzContainer').classList.add('hidden');
            if (document.getElementById('answerTikzContainer')) document.getElementById('answerTikzContainer').classList.add('hidden');
            
            if (document.getElementById('contentTikzPreviewImage')) {
                document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                document.getElementById('contentTikzPreviewImage').src = '';
                document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                document.getElementById('contentTikzStatusText').textContent = '未编译';
            }
            if (document.getElementById('answerTikzPreviewImage')) {
                document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                document.getElementById('answerTikzPreviewImage').src = '';
                document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                document.getElementById('answerTikzStatusText').textContent = '未编译';
            }
            
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
                    window.lastOcrOriginalImagePath = '';
                    window.contentLastCompiledTikzPath = '';
                    window.answerLastCompiledTikzPath = '';
                    // Load values to editor
                    document.getElementById('editContent').value = fullItem.content;
                    document.getElementById('editSource').value = fullItem.source || '';
                    document.getElementById('editAnswerMarkdown').value = fullItem.answer_markdown || '';
                    document.getElementById('editReview').value = fullItem.review || '';
                    if (document.getElementById('editContentTikzCode')) {
                        document.getElementById('editContentTikzCode').value = fullItem.tikz_code || '';
                    }
                    if (document.getElementById('editAnswerTikzCode')) {
                        document.getElementById('editAnswerTikzCode').value = '';
                    }
                    
                    // Reset TikZ Preview on load
                    if (document.getElementById('contentTikzPreviewImage')) {
                        document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                        document.getElementById('contentTikzPreviewImage').src = '';
                        document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                        document.getElementById('contentTikzStatusText').textContent = fullItem.tikz_code ? '已加载' : '未编译';
                    }
                    if (document.getElementById('answerTikzPreviewImage')) {
                        document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                        document.getElementById('answerTikzPreviewImage').src = '';
                        document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                        document.getElementById('answerTikzStatusText').textContent = '未编译';
                    }
                    
                    uploadedImages = fullItem.image_paths || [];
                    renderIllustrationBadges();
                    
                    // Show or hide Content TikZ container dynamically on load
                    const contentContainer = document.getElementById('contentTikzContainer');
                    if (contentContainer) {
                        const hasOriginalImage = uploadedImages.some(path => !path.includes('/tikz_'));
                        if (fullItem.tikz_code || hasOriginalImage) {
                            contentContainer.classList.remove('hidden');
                        } else {
                            contentContainer.classList.add('hidden');
                        }
                    }
                    const answerContainer = document.getElementById('answerTikzContainer');
                    if (answerContainer) {
                        answerContainer.classList.add('hidden');
                    }
                    
                    // Cascade bindings
                    document.getElementById('editQType').value = fullItem.question_type;
                    document.getElementById('editDifficulty').value = fullItem.difficulty;
                    if (document.getElementById('editTags')) {
                        document.getElementById('editTags').value = fullItem.tags || '';
                    }
                    
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
                    
                    let paperTagsHtml = '';
                    if (fullItem.tags) {
                        const tagList = fullItem.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                        tagList.forEach(tag => {
                            paperTagsHtml += `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-250/60 flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[8px] text-amber-500 mr-1"></i>${tag}</span>`;
                        });
                    }

                    badges.innerHTML = `
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">编号：#${fullItem.seq_num}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-brand-50 text-brand-700">题型：${getTypeText(fullItem.question_type)}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">难度：${getDifficultyText(fullItem.difficulty)}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 inline-flex items-center"><i class="fa-regular fa-clock mr-1"></i>录入于：${formatChineseDate(fullItem.created_at)}</span>
                        ${paperTagsHtml}
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
                const tikzCode = document.getElementById('editContentTikzCode') ? document.getElementById('editContentTikzCode').value : '';
                const tags = document.getElementById('editTags') ? document.getElementById('editTags').value.trim() : '';
                
                if (!content.trim()) {
                    showToast('保存失败：题干内容不能为空！', 'error');
                    resolve(false);
                    return;
                }
                
                // Check if Compulsory or Chapter classifications are missing
                if (!skipCheck && (!compulsory || !chapter)) {
                    const choice = await showMissingCompulsoryModal();
                    if (choice === 'manual') {
                        if (!compulsory) {
                            const compSelect = document.getElementById('editCompulsory');
                            if (compSelect) {
                                compSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                compSelect.classList.remove('border-slate-200');
                                compSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    compSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    compSelect.classList.add('border-slate-200');
                                }, 2500);
                                compSelect.focus();
                            }
                        } else if (!chapter) {
                            const chapSelect = document.getElementById('editChapter');
                            if (chapSelect) {
                                chapSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                chapSelect.classList.remove('border-slate-200');
                                chapSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    chapSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    chapSelect.classList.add('border-slate-200');
                                }, 2500);
                                chapSelect.focus();
                            }
                        }
                    } else if (choice === 'ai') {
                        // Automatically open AI classify modal and trigger AI analysis
                        openClassifyModal();
                        runAIClassify();
                    }
                    resolve(false);
                    return;
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
                formData.append('tikz_code', tikzCode);
                formData.append('tags', tags);
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
                    
                    const qtypeLabels = {
                        'single_choice': '单选题',
                        'multi_choice': '多选题',
                        'fill_in_blank': '填空题',
                        'detailed_answer': '解答题'
                    };
                    const label = qtypeLabels[data.question_type] || '未知题型';
                    document.getElementById('recQType').textContent = label;
                    
                    const reminder = document.getElementById('recQTypeReminder');
                    if (data.question_type === 'single_choice' || data.question_type === 'multi_choice') {
                        reminder.classList.remove('hidden');
                    } else {
                        reminder.classList.add('hidden');
                    }
                    
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
            const qtypeSelect = document.getElementById('editQType');
            
            const comp = temporaryClassifyData.compulsory;
            const chap = temporaryClassifyData.chapter;
            
            // Apply question type
            if (temporaryClassifyData.question_type && qtypeSelect) {
                qtypeSelect.value = temporaryClassifyData.question_type;
                if (typeof qtypeSelect.onchange === 'function') {
                    qtypeSelect.onchange();
                }
            }
            
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
            showToast('AI 推荐章节及题型已采纳！');
            
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
            const copyBtn = document.getElementById('copyAnalysisBtn');
            
            if (content.classList.contains('hidden')) {
                content.classList.remove('hidden');
                icon.className = 'fa-solid fa-eye-slash';
                text.textContent = '隐藏解析';
                if (copyBtn) copyBtn.classList.remove('hidden');
            } else {
                content.classList.add('hidden');
                icon.className = 'fa-solid fa-eye';
                text.textContent = '查看解析';
                if (copyBtn) copyBtn.classList.add('hidden');
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
            if (typeof performOrphanedTempCropsCleanup === 'function') {
                performOrphanedTempCropsCleanup();
            }
            const modal = document.getElementById('latexImportModal');
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        // PDF & Crop Global States
        window.currentPdfFile = null;
        window.pdfPageImages = [];
        window.currentPdfTaskId = null;
        window.activeCropQuestionIndex = null;
        window.tempCroppedPathsThisSession = [];

        // Crop Selection variables
        let isDrawing = false;
        let startX = 0;
        let startY = 0;
        let rectLeft = 0;
        let rectTop = 0;
        let rectWidth = 0;
        let rectHeight = 0;
        let activePageIndex = 0;
        let baseWidth = 0;
        let baseHeight = 0;
        let zoomFactor = 1.0;

        window.zoomPdfCropIn = function() {
            zoomFactor = Math.min(3.0, zoomFactor + 0.2);
            applyZoom();
        };

        window.zoomPdfCropOut = function() {
            zoomFactor = Math.max(0.5, zoomFactor - 0.2);
            applyZoom();
        };

        window.resetPdfCropZoom = function() {
            zoomFactor = 1.0;
            applyZoom();
        };

        function applyZoom() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            const zoomText = document.getElementById('pdfZoomFactorText');
            if (!img || !container || baseWidth === 0) return;
            
            const w = baseWidth * zoomFactor;
            const h = baseHeight * zoomFactor;
            
            img.style.width = `${w}px`;
            img.style.height = `${h}px`;
            img.style.maxWidth = 'none';
            img.style.maxHeight = 'none';
            
            container.style.width = `${w}px`;
            container.style.height = `${h}px`;
            
            if (zoomText) {
                zoomText.textContent = `${Math.round(zoomFactor * 100)}%`;
            }
            
            clearPdfCropSelection();
        }

        function openPdfCropModalForQuestion(questionIndex) {
            window.activeCropQuestionIndex = questionIndex;
            activePageIndex = 0;
            zoomFactor = 1.0;
            baseWidth = 0;
            baseHeight = 0;
            window.lastCropLoadedSrc = '';
            
            // Render sidebar page thumbnails
            renderPdfPagesThumbnails();
            
            // Setup drawing listeners FIRST to avoid load race conditions
            setupPdfCropDrawListeners();
            
            // Load the first page (triggers src change and onload cleanly)
            loadPdfCropPage(0);
            
            // Show modal
            const modal = document.getElementById('pdfCropModal');
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closePdfCropModal() {
            const modal = document.getElementById('pdfCropModal');
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
                clearPdfCropSelection();
            }, 300);
        }

        function renderPdfPagesThumbnails() {
            const container = document.getElementById('pdfPagesThumbnailsContainer');
            container.innerHTML = '';
            
            window.pdfPageImages.forEach((url, i) => {
                const thumb = document.createElement('div');
                thumb.className = `cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white ${i === activePageIndex ? 'border-brand-500 shadow-md ring-2 ring-brand-500/20' : 'border-slate-200'}`;
                thumb.innerHTML = `
                    <img src="${url}" class="w-full h-full object-cover">
                    <div class="absolute bottom-1 right-1 bg-black/60 text-white text-[8px] px-1 rounded font-bold">P${i + 1}</div>
                `;
                thumb.onclick = () => {
                    loadPdfCropPage(i);
                };
                container.appendChild(thumb);
            });
        }

        function loadPdfCropPage(pageIdx) {
            activePageIndex = pageIdx;
            
            // Update active thumbnail border class
            const thumbnails = document.getElementById('pdfPagesThumbnailsContainer').children;
            for (let i = 0; i < thumbnails.length; i++) {
                if (i === pageIdx) {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-brand-500 shadow-md ring-2 ring-brand-500/20';
                } else {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-slate-200';
                }
            }
            
            document.getElementById('pdfCropPageIndicator').textContent = `第 ${pageIdx + 1} / ${window.pdfPageImages.length} 页`;
            
            const img = document.getElementById('pdfCropActiveImage');
            img.src = window.pdfPageImages[pageIdx];
            
            clearPdfCropSelection();
        }

        function setupPdfCropDrawListeners() {
            const wrapper = document.getElementById('pdfCropCanvasWrapper');
            if (!wrapper) return;
            
            // Recreate wrapper to drop old listeners clean
            const newWrapper = wrapper.cloneNode(true);
            wrapper.parentNode.replaceChild(newWrapper, wrapper);
            
            const activeWrapper = document.getElementById('pdfCropCanvasWrapper');
            const activeContainer = document.getElementById('pdfCropImageContainer');
            const activeOverlay = document.getElementById('pdfCropOverlayRect');
            const activeImg = document.getElementById('pdfCropActiveImage');
            
            // Bind trackpad pinch zoom
            activeWrapper.addEventListener('wheel', (e) => {
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    const zoomSpeed = 0.03;
                    if (e.deltaY < 0) {
                        zoomFactor = Math.min(3.0, zoomFactor + zoomSpeed);
                    } else {
                        zoomFactor = Math.max(0.5, zoomFactor - zoomSpeed);
                    }
                    applyZoom();
                }
            }, { passive: false });
            
            // Bind image onload
            activeImg.onload = function() {
                if (baseWidth === 0 || activeImg.src !== window.lastCropLoadedSrc) {
                    // Reset style to read original viewport-fitted size
                    activeImg.style.width = '';
                    activeImg.style.height = '';
                    activeImg.style.maxWidth = '';
                    activeImg.style.maxHeight = '';
                    
                    baseWidth = activeImg.clientWidth || 600;
                    baseHeight = activeImg.clientHeight || 800;
                    window.lastCropLoadedSrc = activeImg.src;
                }
                applyZoom();
            };
            
            // Bind drawing select listeners
            activeContainer.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return; // Only left click
                isDrawing = true;
                
                const rect = activeContainer.getBoundingClientRect();
                startX = e.clientX - rect.left;
                startY = e.clientY - rect.top;
                
                rectLeft = startX;
                rectTop = startY;
                rectWidth = 0;
                rectHeight = 0;
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = '0px';
                activeOverlay.style.height = '0px';
                activeOverlay.classList.remove('hidden');
                
                e.preventDefault();
            });
            
            window.addEventListener('mousemove', (e) => {
                if (!isDrawing) return;
                
                const rect = activeContainer.getBoundingClientRect();
                let currentX = e.clientX - rect.left;
                let currentY = e.clientY - rect.top;
                
                currentX = Math.max(0, Math.min(currentX, rect.width));
                currentY = Math.max(0, Math.min(currentY, rect.height));
                
                rectLeft = Math.min(startX, currentX);
                rectTop = Math.min(startY, currentY);
                rectWidth = Math.abs(startX - currentX);
                rectHeight = Math.abs(startY - currentY);
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = `${rectWidth}px`;
                activeOverlay.style.height = `${rectHeight}px`;
            });
            
            window.addEventListener('mouseup', () => {
                if (!isDrawing) return;
                isDrawing = false;
                
                if (rectWidth > 15 && rectHeight > 15) {
                    document.getElementById('pdfCropConfirmBtn').disabled = false;
                    document.getElementById('pdfCropClearBtn').disabled = false;
                } else {
                    clearPdfCropSelection();
                }
            });
        }

        function clearPdfCropSelection() {
            const overlay = document.getElementById('pdfCropOverlayRect');
            if (overlay) {
                overlay.classList.add('hidden');
                overlay.style.width = '0px';
                overlay.style.height = '0px';
            }
            rectWidth = 0;
            rectHeight = 0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            if (confirmBtn) confirmBtn.disabled = true;
            
            const clearBtn = document.getElementById('pdfCropClearBtn');
            if (clearBtn) clearBtn.disabled = true;
        }

        function submitPdfCropCoordinates() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            
            // Adjust coords relative to base (un-zoomed) dimensions
            const containerRect = container.getBoundingClientRect();
            
            const xmin = (rectLeft / containerRect.width) * 100.0;
            const ymin = (rectTop / containerRect.height) * 100.0;
            const xmax = ((rectLeft + rectWidth) / containerRect.width) * 100.0;
            const ymax = ((rectTop + rectHeight) / containerRect.height) * 100.0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>正在裁剪...</span>';
            
            fetch('/api/ai/manual-crop-pdf', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({
                    task_id: window.currentPdfTaskId,
                    page_index: activePageIndex,
                    ymin: ymin,
                    xmin: xmin,
                    ymax: ymax,
                    xmax: xmax
                })
            })
            .then(r => {
                if (!r.ok) throw new Error("裁剪失败");
                return r.json();
            })
            .then(data => {
                if (data.status === 'success') {
                    showToast("裁剪并生成配图成功！已自动关联至此题卡。");
                    
                    const croppedUrl = data.image_path;
                    window.tempCroppedPathsThisSession.push(croppedUrl);
                    
                    const qIdx = window.activeCropQuestionIndex;
                    if (qIdx !== null && parsedQuestionsData[qIdx]) {
                        const q = parsedQuestionsData[qIdx];
                        if (!q.image_paths) q.image_paths = [];
                        
                        if (!q.image_paths.includes(croppedUrl)) {
                            q.image_paths.push(croppedUrl);
                        }
                        
                        // Append the image tag to content textarea to render in card preview
                        const card = document.getElementById(`parsed-card-${qIdx}`);
                        if (card) {
                            const textarea = card.querySelector('.card-content-textarea');
                            if (textarea) {
                                textarea.value = textarea.value.trim() + `\n\n![插图](${croppedUrl})\n\n`;
                                textarea.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        const badgesContainer = document.getElementById(`card-images-badges-${qIdx}`);
                        if (badgesContainer) {
                            badgesContainer.innerHTML = '';
                            q.image_paths.forEach(path => {
                                const filename = path.split('/').pop();
                                const badge = document.createElement('div');
                                badge.className = "flex items-center space-x-1 px-2 py-0.5 bg-slate-100 border rounded-full text-[9px] font-semibold text-slate-500 hover:bg-white transition-colors cursor-pointer select-none";
                                badge.innerHTML = `<i class="fa-solid fa-image text-slate-400"></i><span class="truncate max-w-[80px]" title="${filename}">${filename}</span>`;
                                badgesContainer.appendChild(badge);
                            });
                        }
                    }
                    
                    closePdfCropModal();
                } else {
                    throw new Error(data.message || "裁剪错误");
                }
            })
            .catch(err => {
                console.error(err);
                showToast(`手动截图报错: ${err.message}`, 'error');
            })
            .finally(() => {
                confirmBtn.innerHTML = '<i class="fa-solid fa-crop-simple mr-1.5"></i><span>确认截取配图</span>';
            });
        }

        function performOrphanedTempCropsCleanup() {
            const tempPaths = [];
            parsedQuestionsData.forEach(q => {
                if (!q.saved && q.image_paths) {
                    q.image_paths.forEach(p => {
                        if (p.includes('/tmp/pdf_crop_')) {
                            tempPaths.push(p);
                        }
                    });
                }
            });
            
            if (window.tempCroppedPathsThisSession && window.tempCroppedPathsThisSession.length > 0) {
                window.tempCroppedPathsThisSession.forEach(p => {
                    if (!tempPaths.includes(p)) {
                        tempPaths.push(p);
                    }
                });
            }
            
            if (tempPaths.length === 0) return;
            
            fetch('/api/ai/clear-temp-crops', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({ paths: tempPaths })
            })
            .then(r => r.json())
            .then(res => {
                console.log("[Storage Cleanup] Server cleaned temporary crops:", res);
                window.tempCroppedPathsThisSession = [];
            })
            .catch(err => {
                console.error("[Storage Cleanup] Error:", err);
            });
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

            // LaTeX / PDF File drag & select
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
                if (file && (file.name.endsWith('.tex') || file.name.endsWith('.pdf'))) {
                    handleTexFileSelect(file);
                } else {
                    showToast('请拖入有效的 .tex 或 .pdf 格式试卷文件！', 'warning');
                }
            });

            function handleTexFileSelect(file) {
                if (!file) return;
                
                if (file.name.endsWith('.pdf')) {
                    window.currentPdfFile = file;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-pdf text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.value = `[PDF 试卷已成功载入: ${file.name}]\n总页数、高清转换与插图定位将会在点击“开始智能拆解试卷”后于后台异步执行。`;
                    latexTextarea.disabled = true;
                    
                    const titleInput = document.getElementById('importPaperTitle');
                    if (!titleInput.value) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
                    }
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.remove('hidden');
                } else {
                    window.currentPdfFile = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-circle-check text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.disabled = false;
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        latexTextarea.value = e.target.result;
                        const autoTitle = extractTitleFromLatex(e.target.result);
                        if (autoTitle) {
                            const titleInput = document.getElementById('importPaperTitle');
                            titleInput.value = autoTitle;
                            showToast(`已自动从 LaTeX 文件中读取试卷标题: ${autoTitle}`);
                        }
                    };
                    reader.readAsText(file);
                }
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

            if (!latex && !window.currentPdfFile) {
                showToast('请粘贴或上传 LaTeX 试卷内容，或拖入 PDF 文件！', 'warning');
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

            const generateAnswersCheckbox = document.getElementById('importGenerateAnswers');
            const generateAnswers = generateAnswersCheckbox ? generateAnswersCheckbox.checked : false;

            // Handle PDF branch
            if (window.currentPdfFile) {
                document.getElementById('importLoadingText').textContent = '正在上传 PDF 试卷并创建处理任务...';
                appendImportLog('开始上传 PDF 试卷文件...', 'info');
                document.getElementById('importProgressBarContainer').classList.remove('hidden');
                document.getElementById('importProgressBar').style.width = '0%';

                const pdfFormData = new FormData();
                pdfFormData.append('file', window.currentPdfFile);
                pdfFormData.append('generate_answers', generateAnswers ? "true" : "false");
                
                const pdfPageRangeInput = document.getElementById('pdfPageRange');
                const pageRange = pdfPageRangeInput ? pdfPageRangeInput.value.trim() : '';
                if (pageRange) {
                    pdfFormData.append('page_range', pageRange);
                }

                fetch('/api/upload/pdf-task', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: pdfFormData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(taskData => {
                    if (taskData.status === 'success') {
                        const taskId = taskData.task_id;
                        window.currentPdfTaskId = taskId;
                        appendImportLog(`任务已成功创建！任务 ID: ${taskId}，开始轮询后台分析进度...`, 'success');
                        pollPdfTaskStatus(taskId, title);
                    } else {
                        throw new Error(taskData.message || '创建 PDF 解析任务失败');
                    }
                })
                .catch(err => {
                    console.error(err);
                    appendImportLog(`PDF 任务创建失败: ${err.message}`, 'error');
                    
                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = 'PDF 上传解析出错！';

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
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
                return;
            }

            // Normal LaTeX branch
            document.getElementById('importLoadingText').textContent = '正在上传配套图片并整理文件名映射...';
            appendImportLog('开始检查配套图片...', 'info');
            document.getElementById('importProgressBarContainer').classList.add('hidden');

            let uploadPromise = Promise.resolve({});
            if (batchSelectedImages.length > 0) {
                appendImportLog(`检测到 ${batchSelectedImages.length} 张配图，开始多线程上传中...`, 'info');
                const imgFormData = new FormData();
                batchSelectedImages.forEach(file => {
                    imgFormData.append('files', file);
                });

                uploadPromise = fetch('/api/upload/batch', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
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
                    let parseModelFriendly = systemPreferParseModel.includes('/') ? systemPreferParseModel.split('/').pop() : systemPreferParseModel;
                    let parseBrand = 'AI';
                    document.getElementById('importLoadingText').textContent = `${parseBrand} 正在智能分析并拆解试卷，请稍候...`;
                    appendImportLog(`正在调用 ${parseModelFriendly} 教研大模型进行试题智能分割与属性匹配...`, 'info');
                    appendImportLog('大纲映射范围：高中人教版A 必修一至选择性必修三。请耐心等候...', 'info');

                    const parseFormData = new FormData();
                    parseFormData.append('latex_content', latex);
                    parseFormData.append('paper_title', title);
                    parseFormData.append('image_mapping_json', JSON.stringify(imageMapping));
                    parseFormData.append('generate_answers', generateAnswers ? "true" : "false");

                    return fetch('/api/ai/parse-paper', {
                        method: 'POST',
                        headers: {
                            'X-Local-Token': localStorage.getItem('local_token') || ''
                        },
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

                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = '试卷拆解中断！';

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

        function pollPdfTaskStatus(taskId, paperTitle) {
            let lastLog = '';
            const runBtn = document.getElementById('runParseBtn');
            
            const timer = setInterval(() => {
                fetch(`/api/tasks/${taskId}/status`)
                .then(r => {
                    if (!r.ok) throw new Error("获取任务进度失败");
                    return r.json();
                })
                .then(task => {
                    if (task.progress !== undefined) {
                        document.getElementById('importProgressBar').style.width = `${task.progress}%`;
                    }
                    
                    if (task.log && task.log !== lastLog) {
                        lastLog = task.log;
                        appendImportLog(task.log, 'info');
                        document.getElementById('importLoadingText').textContent = task.log;
                    }
                    
                    if (task.page_images && task.page_images.length > 0) {
                        window.pdfPageImages = task.page_images;
                    }
                    
                    if (task.status === 'completed') {
                        clearInterval(timer);
                        parsedQuestionsData = task.data || [];
                        appendImportLog(`PDF 试卷智能分析并拆解成功！共分析出 ${parsedQuestionsData.length} 道高定数学题。`, 'success');
                        
                        renderParsedQuestionsList(parsedQuestionsData);
                        
                        document.getElementById('importLoadingState').classList.add('hidden');
                        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
                        
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                    } else if (task.status === 'error') {
                        clearInterval(timer);
                        appendImportLog(`分析失败: ${task.error || '未知错误'}`, 'error');
                        
                        const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                        if (loadingIcon) {
                            loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                            loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                        }
                        document.getElementById('importLoadingText').textContent = 'PDF 试卷分析中断！';

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
                        
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                        showToast(`PDF 拆解分析失败: ${task.error || '未知错误'}`, 'error');
                    }
                })
                .catch(err => {
                    console.error(err);
                });
            }, 1500);
        }

        function renderImagesList() {
            const imagesListContainer = document.getElementById('importImagesList');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            
            if (!imagesListContainer || !imagesCountName || !imagesFileIcon) return;

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

        function clearAllImportInputs() {
            // 清空左侧输入栏
            const titleInput = document.getElementById('importPaperTitle');
            if (titleInput) titleInput.value = '';

            const latexTextarea = document.getElementById('importLatexContent');
            if (latexTextarea) {
                latexTextarea.value = '';
                latexTextarea.disabled = false;
            }

            const texFileInput = document.getElementById('texFileInput');
            if (texFileInput) texFileInput.value = '';

            const imagesFileInput = document.getElementById('imagesFileInput');
            if (imagesFileInput) imagesFileInput.value = '';

            // 重置 .tex 拖拽显示样式
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            if (texFileName) {
                texFileName.textContent = "点击或拖放拖入 .tex 或 .pdf 格式试卷文件";
                texFileName.className = "text-xs text-slate-600 font-medium";
            }
            if (texFileIcon) {
                texFileIcon.className = "fa-solid fa-file-pdf text-slate-400 text-xl mb-1.5";
            }

            // 清空批量配图
            batchSelectedImages = [];
            // 重置图片展示列表与状态
            renderImagesList();
            
            // 重置 PDF 状态
            window.currentPdfFile = null;
            window.pdfPageImages = [];
            window.currentPdfTaskId = null;
            window.activeCropQuestionIndex = null;
            window.tempCroppedPathsThisSession = [];
            
            const pdfRange = document.getElementById('pdfPageRange');
            if (pdfRange) pdfRange.value = '';
            const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
            if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
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

            // 清空右侧解析题目卡片 DOM
            const container = document.getElementById('parsedCardsContainer');
            if (container) {
                container.innerHTML = '';
            }
            const countBadge = document.getElementById('parsedCountBadge');
            if (countBadge) {
                countBadge.textContent = '共 0 题';
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
                let qTypeOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.question_types) {
                    window.systemMetadata.question_types.forEach(item => {
                        qTypeOptionsHtml += `<option value="${item.value}" ${q.question_type === item.value ? 'selected' : ''}>${item.label}</option>`;
                    });
                } else {
                    qTypeOptionsHtml = `
                        <option value="single_choice" ${q.question_type === 'single_choice' ? 'selected' : ''}>单选题</option>
                        <option value="multi_choice" ${q.question_type === 'multi_choice' ? 'selected' : ''}>多选题</option>
                        <option value="fill_in_blank" ${q.question_type === 'fill_in_blank' ? 'selected' : ''}>填空题</option>
                        <option value="detailed_answer" ${q.question_type === 'detailed_answer' ? 'selected' : ''}>解答题</option>
                    `;
                }

                let difficultyOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.difficulties) {
                    window.systemMetadata.difficulties.forEach(item => {
                        difficultyOptionsHtml += `<option value="${item.value}" ${q.difficulty === item.value ? 'selected' : ''}>${item.label}</option>`;
                    });
                } else {
                    difficultyOptionsHtml = `
                        <option value="easy_error" ${q.difficulty === 'easy_error' ? 'selected' : ''}>易错题</option>
                        <option value="challenge" ${q.difficulty === 'challenge' ? 'selected' : ''}>挑战题</option>
                        <option value="qiangji" ${q.difficulty === 'qiangji' ? 'selected' : ''}>强基题</option>
                    `;
                }

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
                            ${qTypeOptionsHtml}
                        </select>
                        <select class="card-difficulty glass-select px-2 py-1.5 rounded-lg text-2xs font-semibold">
                            ${difficultyOptionsHtml}
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
                        <div class="flex items-center space-x-2">
                            ${window.pdfPageImages && window.pdfPageImages.length > 0 ? `
                                <button onclick="openPdfCropModalForQuestion(${index})" class="glass-btn text-amber-700 font-bold px-3 py-1.5 rounded-lg text-2xs flex items-center space-x-1" title="查看 PDF 页面并拖拽框选截图">
                                    <i class="fa-solid fa-scissors"></i>
                                    <span>手动截图</span>
                                </button>
                            ` : ''}
                            <button onclick="saveParsedQuestion(${index})" class="card-save-btn px-4 py-1.5 rounded-lg text-2xs flex items-center space-x-1 shrink-0 ${q.saved ? 'bg-emerald-50 text-emerald-700 font-bold border border-emerald-250 hover:bg-emerald-100' : 'glass-btn text-brand-700 font-bold'}">
                                <i class="fa-solid ${q.saved ? 'fa-rotate-right' : 'fa-file-arrow-up'}"></i>
                                <span>${q.saved ? '再次导入' : '导入此题'}</span>
                            </button>
                        </div>
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
            
            // Extract card index to find its image_paths dynamically
            const indexStr = card.id ? card.id.replace('parsed-card-', '') : '';
            const index = indexStr ? parseInt(indexStr) : null;
            const q = (index !== null && !isNaN(index)) ? parsedQuestionsData[index] : null;
            
            // For content
            if (!contentText.trim()) {
                contentPrev.innerHTML = '<span class="text-slate-400 italic text-2xs">题干预览将在此实时渲染...</span>';
            } else {
                try {
                    let html = parseMarkdownWithMath(contentText);
                    
                    // Automatically append associated image thumbnails to preview if not already rendered in markdown HTML
                    if (q && q.image_paths && q.image_paths.length > 0) {
                        let hasUnrenderedImage = false;
                        let imgHtml = '<div class="flex flex-wrap gap-2 mt-3 pt-2.5 border-t border-dashed border-slate-200/60">';
                        q.image_paths.forEach(p => {
                            if (p && !html.includes(p)) {
                                hasUnrenderedImage = true;
                                imgHtml += `
                                    <div class="relative group border border-slate-150 rounded-lg overflow-hidden bg-white max-w-[120px] aspect-[4/3] flex items-center justify-center shadow-2xs hover:shadow-sm transition-all duration-300">
                                        <img src="${p}" class="max-h-full max-w-full object-contain cursor-zoom-in hover:scale-105 transition-transform duration-300" onclick="window.open('${p}', '_blank')" title="点击在新标签页中查看大图">
                                    </div>`;
                            }
                        });
                        imgHtml += '</div>';
                        if (hasUnrenderedImage) {
                            html += imgHtml;
                        }
                    }
                    
                    contentPrev.innerHTML = html;
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
            if (!q) return Promise.resolve(true);

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
                    
                    saveBtn.className = 'card-save-btn px-4 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 font-bold text-2xs border border-emerald-250 hover:bg-emerald-100 transition-colors';
                    saveBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> <span>再次导入</span>';
                    saveBtn.disabled = false;
                    
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

        function confirmClearAllParsed() {
            if (confirm('确定要清除所有拆解出来的题目，并返回初始录入界面吗？\n清除后，当前列表中的草稿题目将丢失。')) {
                if (typeof performOrphanedTempCropsCleanup === 'function') {
                    performOrphanedTempCropsCleanup();
                }
                clearAllImportInputs();
                resetImportState(true);
            }
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

            // ================== TikZ Geometry Drawing & AI Correction Helpers (双通道分离设计) ==================
            window.extractTikzCodeFromTextarea = function(textareaId) {
                const textarea = document.getElementById(textareaId);
                if (!textarea) return;
                
                let text = textarea.value;
                const tikzRegex = /(\\begin\s*\{\s*tikzpicture\s*\}[\s\S]*?\\end\s*\{\s*tikzpicture\s*\})/i;
                const match = text.match(tikzRegex);
                
                if (match) {
                    const tikzBlock = match[1].trim();
                    const isContent = (textareaId === 'editContent');
                    const targetInputId = isContent ? 'editContentTikzCode' : 'editAnswerTikzCode';
                    const targetContainerId = isContent ? 'contentTikzContainer' : 'answerTikzContainer';
                    
                    // Show Container
                    const container = document.getElementById(targetContainerId);
                    if (container) container.classList.remove('hidden');
                    
                    // Fill input
                    const tikzInput = document.getElementById(targetInputId);
                    if (tikzInput) {
                        tikzInput.value = tikzBlock;
                        tikzInput.dispatchEvent(new Event('input'));
                    }
                    
                    // Clear from textarea
                    text = text.replace(tikzRegex, '').trim();
                    textarea.value = text;
                    textarea.dispatchEvent(new Event('input'));
                    
                    // Auto-compile
                    const compileFn = isContent ? window.renderContentTikzToImage : window.renderAnswerTikzToImage;
                    if (typeof compileFn === 'function') {
                        const targetName = isContent ? '题干' : '解答';
                        showToast(`🎉 检测到${targetName}中的 TikZ 代码！已自动提取并开始编译。`, 'info');
                        setTimeout(() => {
                            compileFn();
                            
                            // Scroll to focus
                            if (tikzInput) {
                                tikzInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                tikzInput.focus();
                            }
                        }, 200);
                    }
                }
            };

            // 题干清理与编译
            window.clearContentTikzCode = function() {
                if (confirm("确定要清空题干 TikZ 代码吗？")) {
                    document.getElementById('editContentTikzCode').value = '';
                    document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                    document.getElementById('contentTikzPreviewImage').src = '';
                    document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                    document.getElementById('contentTikzStatusText').textContent = '已清空';
                }
            };

            window.renderContentTikzToImage = function() {
                const tikzCode = document.getElementById('editContentTikzCode').value;
                if (!tikzCode.trim()) {
                    showToast('请输入题干 TikZ 绘图代码后重试。', 'error');
                    return;
                }
                
                const btn = document.getElementById('btnRenderContentTikz');
                const statusText = document.getElementById('contentTikzStatusText');
                const placeholder = document.getElementById('contentTikzPreviewPlaceholder');
                const previewImg = document.getElementById('contentTikzPreviewImage');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>编译中...</span>';
                statusText.textContent = '编译中...';
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                
                fetch('/api/render_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '编译失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-play"></i> <span>编译并插入题干</span>';
                    
                    if (data.status === 'success') {
                        showToast('题干 TikZ 几何图编译成功，已插入插图列表！');
                        statusText.textContent = '编译成功';
                        
                        placeholder.classList.add('hidden');
                        previewImg.src = data.image_path + '?t=' + new Date().getTime();
                        previewImg.classList.remove('hidden');
                        
                        const cleanPath = data.image_path;
                        const contentInput = document.getElementById('editContent');
                        const oldPath = window.contentLastCompiledTikzPath;
                        
                        // Replace previous compiled path if exists
                        if (oldPath && oldPath !== cleanPath) {
                            const idx = uploadedImages.indexOf(oldPath);
                            if (idx > -1) {
                                uploadedImages.splice(idx, 1);
                            }
                            if (contentInput && contentInput.value.includes(oldPath)) {
                                contentInput.value = contentInput.value.split(oldPath).join(cleanPath);
                                contentInput.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        if (!uploadedImages.includes(cleanPath)) {
                            uploadedImages.push(cleanPath);
                        }
                        renderIllustrationBadges();
                        
                        if (contentInput && !contentInput.value.includes(cleanPath)) {
                            contentInput.value += `\n\n![](${cleanPath})`;
                            contentInput.dispatchEvent(new Event('input'));
                        }
                        
                        window.contentLastCompiledTikzPath = cleanPath;
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-play"></i> <span>编译并插入题干</span>';
                    statusText.textContent = '编译出错';
                    placeholder.classList.remove('hidden');
                    previewImg.classList.add('hidden');
                    showToast('题干 TikZ 编译出错: ' + err.message, 'error');
                });
            };

            window.correctContentTikzWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = uploadedImages.filter(path => !path.includes('/tikz_'));
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('无法纠错：当前题目未检测到任何原始截图作为参考比对模板。', 'error');
                    return;
                }
                
                const tikzCode = document.getElementById('editContentTikzCode').value;
                const btn = document.getElementById('btnCorrectContentTikz');
                const statusText = document.getElementById('contentTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>纠错中...</span>';
                statusText.textContent = '纠错中...';
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                formData.append('original_image_path', originalPath);
                
                const userPromptInput = document.getElementById('contentTikzUserPrompt');
                const userPrompt = userPromptInput ? userPromptInput.value.trim() : '';
                formData.append('user_prompt', userPrompt);
                
                fetch('/api/correct_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '纠错失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>';
                    
                    if (data.status === 'success') {
                        showToast('AI 纠错完成，已回填并重新编译代码！');
                        document.getElementById('editContentTikzCode').value = data.corrected_code;
                        document.getElementById('editContentTikzCode').dispatchEvent(new Event('input'));
                        window.renderContentTikzToImage();
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>';
                    statusText.textContent = '纠错失败';
                    showToast('AI 纠错出错: ' + err.message, 'error');
                });
            };

            // 解答清理与编译
            window.clearAnswerTikzCode = function() {
                if (confirm("确定要清空解答 TikZ 代码吗？")) {
                    document.getElementById('editAnswerTikzCode').value = '';
                    document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                    document.getElementById('answerTikzPreviewImage').src = '';
                    document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                    document.getElementById('answerTikzStatusText').textContent = '已清空';
                }
            };

            window.renderAnswerTikzToImage = function() {
                const tikzCode = document.getElementById('editAnswerTikzCode').value;
                if (!tikzCode.trim()) {
                    showToast('请输入解答 TikZ 绘图代码后重试。', 'error');
                    return;
                }
                
                const btn = document.getElementById('btnRenderAnswerTikz');
                const statusText = document.getElementById('answerTikzStatusText');
                const placeholder = document.getElementById('answerTikzPreviewPlaceholder');
                const previewImg = document.getElementById('answerTikzPreviewImage');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>编译中...</span>';
                statusText.textContent = '编译中...';
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                
                fetch('/api/render_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '编译失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-play"></i> <span>编译并插入解答</span>';
                    
                    if (data.status === 'success') {
                        showToast('解答 TikZ 几何图编译成功，已插入解答文本中！');
                        statusText.textContent = '编译成功';
                        
                        placeholder.classList.add('hidden');
                        previewImg.src = data.image_path + '?t=' + new Date().getTime();
                        previewImg.classList.remove('hidden');
                        
                        const cleanPath = data.image_path;
                        const answerInput = document.getElementById('editAnswerMarkdown');
                        const oldPath = window.answerLastCompiledTikzPath;
                        
                        // Replace previous compiled path if exists
                        if (oldPath && oldPath !== cleanPath) {
                            if (answerInput && answerInput.value.includes(oldPath)) {
                                answerInput.value = answerInput.value.split(oldPath).join(cleanPath);
                                answerInput.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        if (answerInput && !answerInput.value.includes(cleanPath)) {
                            answerInput.value += `\n\n![](${cleanPath})`;
                            answerInput.dispatchEvent(new Event('input'));
                        }
                        
                        window.answerLastCompiledTikzPath = cleanPath;
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-play"></i> <span>编译并插入解答</span>';
                    statusText.textContent = '编译出错';
                    placeholder.classList.remove('hidden');
                    previewImg.classList.add('hidden');
                    showToast('解答 TikZ 编译出错: ' + err.message, 'error');
                });
            };

            window.correctAnswerTikzWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = uploadedImages.filter(path => !path.includes('/tikz_'));
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('无法纠错：当前题目未检测到任何原始截图作为参考比对模板。', 'error');
                    return;
                }
                
                const tikzCode = document.getElementById('editAnswerTikzCode').value;
                const btn = document.getElementById('btnCorrectAnswerTikz');
                const statusText = document.getElementById('answerTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>纠错中...</span>';
                statusText.textContent = '纠错中...';
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                formData.append('original_image_path', originalPath);
                
                const userPromptInput = document.getElementById('answerTikzUserPrompt');
                const userPrompt = userPromptInput ? userPromptInput.value.trim() : '';
                formData.append('user_prompt', userPrompt);
                
                fetch('/api/correct_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '纠错失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>';
                    
                    if (data.status === 'success') {
                        showToast('AI 纠错完成，已回填并重新编译代码！');
                        document.getElementById('editAnswerTikzCode').value = data.corrected_code;
                        document.getElementById('editAnswerTikzCode').dispatchEvent(new Event('input'));
                        window.renderAnswerTikzToImage();
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>';
                    statusText.textContent = '纠错失败';
                    showToast('AI 纠错出错: ' + err.message, 'error');
                });
            };

            window.drawContentTikzFromImageWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = typeof uploadedImages !== 'undefined' ? uploadedImages.filter(path => !path.includes('/tikz_')) : [];
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('当前题目未检测到任何插图可供 AI 识别绘图。', 'error');
                    return;
                }
                
                const latexContent = document.getElementById('editContent').value;
                const btn = document.getElementById('btnDrawContentTikzFromImage');
                const statusText = document.getElementById('contentTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>识别绘制中...</span>';
                statusText.textContent = '识别绘制中...';
                
                const formData = new FormData();
                formData.append('image_path', originalPath);
                if (latexContent && latexContent.trim()) {
                    formData.append('latex_content', latexContent);
                }
                
                fetch('/api/ai/draw_tikz_from_image', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '识别绘图失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>';
                    
                    if (data.status === 'success') {
                        showToast('AI 识图绘图完成，已生成 TikZ 代码并开始自动编译！');
                        document.getElementById('editContentTikzCode').value = data.tikz_code;
                        document.getElementById('editContentTikzCode').dispatchEvent(new Event('input'));
                        window.renderContentTikzToImage();
                    } else {
                        throw new Error(data.message || '识别绘图失败');
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>';
                    statusText.textContent = '识别绘图失败';
                    showToast('AI 识图绘图出错: ' + err.message, 'error');
                });
            };

            window.drawAnswerTikzFromImageWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = typeof uploadedImages !== 'undefined' ? uploadedImages.filter(path => !path.includes('/tikz_')) : [];
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('当前题目未检测到任何插图可供 AI 识别绘图。', 'error');
                    return;
                }
                
                const latexContent = document.getElementById('editContent').value;
                const btn = document.getElementById('btnDrawAnswerTikzFromImage');
                const statusText = document.getElementById('answerTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>识别绘制中...</span>';
                statusText.textContent = '识别绘制中...';
                
                const formData = new FormData();
                formData.append('image_path', originalPath);
                if (latexContent && latexContent.trim()) {
                    formData.append('latex_content', latexContent);
                }
                
                fetch('/api/ai/draw_tikz_from_image', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '识别绘图失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>';
                    
                    if (data.status === 'success') {
                        showToast('AI 识图绘图完成，已生成 TikZ 代码并开始自动编译！');
                        document.getElementById('editAnswerTikzCode').value = data.tikz_code;
                        document.getElementById('editAnswerTikzCode').dispatchEvent(new Event('input'));
                        window.renderAnswerTikzToImage();
                    } else {
                        throw new Error(data.message || '识别绘图失败');
                    }
                })
                .catch(err => {
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>';
                    statusText.textContent = '识别绘图失败';
                    showToast('AI 识图绘图出错: ' + err.message, 'error');
                });
            };
        });

