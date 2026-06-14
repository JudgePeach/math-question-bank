        function setupUploadHandlers() {
            // Setup Illustration drag and drop
            const illDropZone = document.getElementById('illustrationDropZone');
            const illFileInput = document.getElementById('illustrationFileInput');
            
            illDropZone.onclick = () => illFileInput.click();
            
            illFileInput.onchange = () => {
                if (illFileInput.files.length > 0) {
                    uploadIllustration(illFileInput.files[0]);
                    illFileInput.value = ''; // Reset so same file can be uploaded again
                }
            };
            
            setupDragDropListeners(illDropZone, (file) => uploadIllustration(file));
            setupPasteListener(document.getElementById('editContent'), (file) => uploadIllustration(file));

            // Setup OCR drag and drop
            const ocrDropZone = document.getElementById('ocrDropZone');
            const ocrFileInput = document.getElementById('ocrFileInput');
            
            ocrDropZone.onclick = () => ocrFileInput.click();
            
            ocrFileInput.onchange = () => {
                if (ocrFileInput.files.length > 0) {
                    runOcr(ocrFileInput.files[0]);
                    ocrFileInput.value = ''; // Reset so same file can be uploaded again
                }
            };
            
            setupDragDropListeners(ocrDropZone, (file) => runOcr(file));
            setupPasteListener(document.getElementById('ocrDropZone'), (file) => runOcr(file));

            // Setup Question Content OCR file input and Drop Zone
            const contentOcrDropZone = document.getElementById('contentOcrDropZone');
            const contentOcrFileInput = document.getElementById('contentOcrFileInput');
            
            contentOcrDropZone.onclick = () => contentOcrFileInput.click();
            
            contentOcrFileInput.onchange = () => {
                if (contentOcrFileInput.files.length > 0) {
                    runContentOcr(contentOcrFileInput.files[0]);
                    contentOcrFileInput.value = ''; // Reset so same file can be uploaded again
                }
            };
            
            setupDragDropListeners(contentOcrDropZone, (file) => runContentOcr(file));
            setupPasteListener(contentOcrDropZone, (file) => runContentOcr(file));

            // Setup Image Answer Drag and Drop
            const imageAnswerDropZone = document.getElementById('imageAnswerDropZone');
            const imageAnswerFileInput = document.getElementById('imageAnswerFileInput');
            
            if (imageAnswerDropZone) {
                imageAnswerDropZone.onclick = () => imageAnswerFileInput.click();
                
                imageAnswerFileInput.onchange = () => {
                    if (imageAnswerFileInput.files.length > 0) {
                        for (let i = 0; i < imageAnswerFileInput.files.length; i++) {
                            uploadAnswerImage(imageAnswerFileInput.files[i]);
                        }
                        imageAnswerFileInput.value = ''; // Reset so same file can be uploaded again
                    }
                };
                
                setupDragDropListeners(imageAnswerDropZone, (file) => uploadAnswerImage(file));
                setupPasteListener(imageAnswerDropZone, (file) => uploadAnswerImage(file));
            }

            // Keyboard accessibility for drag & drop zones (allowing Enter or Space key to trigger file selection)
            [illDropZone, ocrDropZone, contentOcrDropZone, imageAnswerDropZone].forEach(zone => {
                if (zone) {
                    zone.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            zone.click();
                        }
                    });
                }
            });

            // Global smart clipboard paste routing for images/screenshots (Tab visibility priority)
            window.addEventListener('paste', (e) => {
                const activeEl = document.activeElement;
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                let hasImage = false;
                let imageFile = null;
                
                for (let index in items) {
                    const item = items[index];
                    if (item.kind === 'file' && item.type.startsWith('image/')) {
                        hasImage = true;
                        imageFile = item.getAsFile();
                        break;
                    }
                }
                
                if (hasImage && imageFile) {
                    // Check active tab visibility to route intelligently first!
                    const contentOcrTab = document.getElementById('contentTabContent-ocr');
                    const answerOcrTab = document.getElementById('tabContent-ocr');
                    const answerImageTab = document.getElementById('tabContent-image');
                    
                    const isContentOcrVisible = contentOcrTab && !contentOcrTab.classList.contains('hidden');
                    const isAnswerOcrVisible = answerOcrTab && !answerOcrTab.classList.contains('hidden');
                    const isAnswerImageVisible = answerImageTab && !answerImageTab.classList.contains('hidden');
                    
                    // Check active focused element context to route precisely
                    if (activeEl) {
                        const inAnswerPanel = activeEl.closest('#answerExplanationPanel');
                        const inQuestionPanel = activeEl.closest('#questionContentPanel');
                        
                        if (inAnswerPanel) {
                            if (isAnswerOcrVisible) {
                                runOcr(imageFile);
                                e.preventDefault();
                                return;
                            } else if (isAnswerImageVisible) {
                                uploadAnswerImage(imageFile);
                                e.preventDefault();
                                return;
                            } else {
                                // Default to OCR in answer area if AI or other tab is focused
                                runOcr(imageFile);
                                e.preventDefault();
                                return;
                            }
                        } else if (inQuestionPanel) {
                            if (isContentOcrVisible) {
                                runContentOcr(imageFile);
                                e.preventDefault();
                                return;
                            } else {
                                uploadIllustration(imageFile);
                                e.preventDefault();
                                return;
                            }
                        }
                    }
                    
                    // Default fallback routing using tab visibility if no specific container focus
                    // 1. If Answer OCR tab is visible, route to Answer OCR (SimpleTex) immediately
                    if (isAnswerOcrVisible) {
                        runOcr(imageFile);
                        e.preventDefault();
                        return;
                    }
                    
                    // 2. If Question Content OCR tab is visible, route to Content OCR immediately
                    if (isContentOcrVisible) {
                        runContentOcr(imageFile);
                        e.preventDefault();
                        return;
                    }

                    // 3. If Image Answer tab is visible, route to Answer Image upload immediately
                    if (isAnswerImageVisible) {
                        uploadAnswerImage(imageFile);
                        e.preventDefault();
                        return;
                    }
                    
                    // 4. If neither tab is active, check active focused element
                    if (activeEl && activeEl.id === 'illustrationDropZone') {
                        uploadIllustration(imageFile);
                        e.preventDefault();
                        return;
                    } else if (activeEl && activeEl.id === 'ocrDropZone') {
                        runOcr(imageFile);
                        e.preventDefault();
                        return;
                    } else if (activeEl && activeEl.id === 'contentOcrDropZone') {
                        runContentOcr(imageFile);
                        e.preventDefault();
                        return;
                    } else if (activeEl && activeEl.id === 'imageAnswerDropZone') {
                        uploadAnswerImage(imageFile);
                        e.preventDefault();
                        return;
                    }
                    
                    // 5. Default fallback: upload as an illustration
                    uploadIllustration(imageFile);
                    e.preventDefault();
                }
            });
        }
        function setupDragDropListeners(zone, onFileReceived) {
            ['dragenter', 'dragover'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            ['dragleave', 'drop'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            zone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files.length > 0) {
                    for (let i = 0; i < files.length; i++) {
                        onFileReceived(files[i]);
                    }
                }
            }, false);
        }

        // Paste clipboard screen captures handler
        function setupPasteListener(element, onFileReceived) {
            element.addEventListener('paste', (e) => {
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                for (let index in items) {
                    const item = items[index];
                    if (item.kind === 'file') {
                        const blob = item.getAsFile();
                        onFileReceived(blob);
                        e.preventDefault();
                        e.stopPropagation();
                    }
                }
            });
        }

        // Clean and strip leading question numbers and exclamation noise from OCR LaTeX results
        function cleanMathOcrText(text) {
            if (!text) return '';
            
            // 1. Strip LaTeX thin space \!, \, and literal ! / ！
            let cleaned = text.replace(/\\!/g, '');
            cleaned = cleaned.replace(/\\,/g, ''); // Remove all \, thin spaces
            cleaned = cleaned.replace(/[!！]/g, '');
            
            // 2. Strip leading question numbers recursively (e.g., "一、 1. " -> "1. " -> "")
            let prev = '';
            while (cleaned !== prev) {
                prev = cleaned;
                cleaned = cleaned.trim();
                
                // Pattern 1: "第 1 题", "第1题", "第1题、" etc.
                cleaned = cleaned.replace(/^第\s*\d+\s*题[\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 2: Chinese numbers "一、", "十一．", etc.
                cleaned = cleaned.replace(/^[一二三四五六七八九十百]+[\s、．\.\,，\:\：\-\—\~]+/i, '');
                
                // Pattern 3: parenthesized or bracketed numbers: (1), （2）, [3], 【4】
                cleaned = cleaned.replace(/^[\(（\[【]\s*\d+\s*[\)）\]】][\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 4: normal digits followed by punctuation: 1., 12、, 3, 4．, etc.
                cleaned = cleaned.replace(/^\d+[\s\.\,，、．\:\：\-\—\~]+/, '');
                
                // Pattern 5: "例 1:", "例题 1:", "例1", "例题1：", etc.
                cleaned = cleaned.replace(/^例(?:题)?\s*\d+[\s\.\,，、．\:\：\-\—\~]*/i, '');
            }
            
            return cleaned.trim();
        }

        // Cancel and abort all active OCR processes (both content and answer OCR)
        function cancelAllOcr() {
            let aborted = false;
            
            // Handle content OCR abort
            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
                contentOcrAbortController = null;
                aborted = true;
                
                // Hide loading text and update status badge for content OCR preview
                const contentOcrLoadingText = document.getElementById('contentOcrStatusLoadingText');
                const contentOcrStatusBadge = document.getElementById('contentOcrStatusBadge');
                if (contentOcrLoadingText) contentOcrLoadingText.classList.add('hidden');
                if (contentOcrStatusBadge) {
                    contentOcrStatusBadge.classList.remove('hidden');
                    contentOcrStatusBadge.textContent = '已取消识别 (点击可更换图片)';
                }
            }
            
            // Handle answer OCR abort
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
                answerOcrAbortController = null;
                aborted = true;
                
                // Hide loading text and update status badge for answer OCR preview
                const ocrStatusLoadingText = document.getElementById('ocrStatusLoadingText');
                const ocrStatusBadge = document.getElementById('ocrStatusBadge');
                if (ocrStatusLoadingText) ocrStatusLoadingText.classList.add('hidden');
                if (ocrStatusBadge) {
                    ocrStatusBadge.classList.remove('hidden');
                    ocrStatusBadge.textContent = '已取消识别 (点击可更换图片)';
                }
            }
            
            if (aborted) {
                // Restore loading indicator and dropzone UI states (but keep image preview)
                const contentOcrDropZone = document.getElementById('contentOcrDropZone');
                const contentOcrLoading = document.getElementById('contentOcrLoadingIndicator');
                if (contentOcrDropZone && contentOcrLoading) {
                    contentOcrLoading.classList.add('hidden');
                    contentOcrDropZone.classList.remove('hidden');
                }
                
                const ocrDropZone = document.getElementById('ocrDropZone');
                const ocrLoading = document.getElementById('ocrLoadingIndicator');
                if (ocrDropZone && ocrLoading) {
                    ocrLoading.classList.add('hidden');
                    ocrDropZone.classList.remove('hidden');
                }
                
                showToast('OCR 识别已取消，按 ESC 可清除图片', 'info');
            } else {
                // If no active OCR process is running, clear all image previews and results completely
                clearContentOcrPreview();
                clearOcrPreview();
                showToast('已清除当前识图状态与图片', 'info');
            }
        }

        // Global Esc key listener for canceling OCR & closing lightbox
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Esc') {
                const lightbox = document.getElementById('imageLightbox');
                if (lightbox && !lightbox.classList.contains('hidden')) {
                    closeLightbox();
                } else {
                    cancelAllOcr();
                }
            }
        });

        // 1. Upload Illustration handler
        function uploadIllustration(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            
            showToast('正在上传插图...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast('图片上传成功！');
                    uploadedImages.push(data.file_path);
                    renderIllustrationBadges();
                    
                    // Insert image markdown tag into textarea where cursor is
                    insertImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        function insertImageTag(filePath) {
            const textarea = document.getElementById('editContent');
            const markdownTag = `\n\n![插图](${filePath})\n\n`;
            
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);
            
            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        function renderIllustrationBadges() {
            const listContainer = document.getElementById('illustrationsList');
            listContainer.innerHTML = '';
            
            uploadedImages.forEach((path, idx) => {
                const filename = path.split('/').pop();
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" onclick="deleteUploadedIllustration(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
        }

        function deleteUploadedIllustration(idx) {
            // We just remove it from active images array
            const deletedPath = uploadedImages[idx];
            uploadedImages.splice(idx, 1);
            renderIllustrationBadges();
            
            // Remove markdown code from editor if user wants
            const textarea = document.getElementById('editContent');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[插图\\]\\(${deletedPath}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('插图已移除');
        }

        // 1.5 Image Answer (No OCR) handler
        function uploadAnswerImage(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            
            showToast('正在上传图片解答...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast('图片解答上传成功！');
                    insertAnswerImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        function insertAnswerImageTag(filePath) {
            const textarea = document.getElementById('editAnswerMarkdown');
            const markdownTag = `\n\n![图片解答](${filePath})\n\n`;
            
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);
            
            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
            
            // Sync answer images array & badges
            syncAnswerImagesFromMarkdown();
        }

        function renderAnswerImageBadges() {
            const listContainer = document.getElementById('imageAnswersList');
            if (!listContainer) return;
            
            listContainer.innerHTML = '';
            
            if (uploadedAnswerImages.length === 0) {
                listContainer.innerHTML = '<p id="noImageAnswerPlaceholder" class="text-xs text-slate-400 italic w-full text-center py-2">暂无已上传的图片解答</p>';
                return;
            }
            
            uploadedAnswerImages.forEach((path, idx) => {
                const filename = path.split('/').pop();
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" onclick="deleteUploadedAnswerImage(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
        }

        function deleteUploadedAnswerImage(idx) {
            const deletedPath = uploadedAnswerImages[idx];
            const textarea = document.getElementById('editAnswerMarkdown');
            
            // Remove markdown code from editor
            const escaped = escapeRegExp(deletedPath);
            textarea.value = textarea.value.replace(new RegExp(`\\\\!\\\\\\[.*?\\\\\\]\\\\(${escaped}\\\\)`, 'g'), '');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[.*?\\]\\(${escaped}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('图片解答已从解析中移除');
            
            // Sync badges
            syncAnswerImagesFromMarkdown();
        }

        function syncAnswerImagesFromMarkdown() {
            const val = document.getElementById('editAnswerMarkdown').value || '';
            const regex = /!\[.*?\]\((.*?)\)/g;
            let match;
            const foundImages = [];
            while ((match = regex.exec(val)) !== null) {
                if (match[1] && match[1].includes('/static/uploads/')) {
                    foundImages.push(match[1]);
                }
            }
            uploadedAnswerImages = foundImages;
            renderAnswerImageBadges();
        }

        // 2. OCR Answer screenshot handler
        function updateOcrPlaceholder(type) {
            const getEngineLabel = (val) => {
                if (val === 'default') {
                    return `默认配置 (当前: ${getEngineLabel(systemPreferEngine)})`;
                } else if (val === 'siliconflow') {
                    return "SiliconFlow 硅基流动云端 (多模态 Qwen 强力识图)";
                } else if (val === 'ali_bailian') {
                    return "阿里百炼 (qwen3-vl-flash 多模态极速识图)";
                } else if (val === 'simpletex') {
                    return "SimpleTex 官方云端 (专注数学公式/手写 OCR 引擎)";
                }
                return "";
            };

            if (type === 'content') {
                const select = document.getElementById('contentOcrEngine');
                const subText = document.getElementById('contentOcrPlaceholderSub');
                if (select && subText) {
                    subText.textContent = getEngineLabel(select.value);
                }
            } else if (type === 'answer') {
                const select = document.getElementById('answerOcrEngine');
                const subText = document.getElementById('answerOcrPlaceholderSub');
                if (select && subText) {
                    subText.textContent = getEngineLabel(select.value);
                }
            }
        }

        function runOcr(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const ocrDropZone = document.getElementById('ocrDropZone');
            const ocrOutput = document.getElementById('ocrOutputBox');
            const ocrResult = document.getElementById('ocrResultText');
            const ocrConf = document.getElementById('ocrConfBadge');
            
            const previewImg = document.getElementById('ocrPreviewImg');
            const previewContainer = document.getElementById('ocrPreviewContainer');
            const placeholder = document.getElementById('ocrPlaceholder');
            const statusBadge = document.getElementById('ocrStatusBadge');
            const loadingText = document.getElementById('ocrStatusLoadingText');
            
            // Read file to show image preview in the upload area IMMEDIATELY
            const reader = new FileReader();
            reader.onload = (e) => {
                if (previewImg && previewContainer && placeholder) {
                    previewImg.src = e.target.result;
                    placeholder.classList.add('hidden');
                    previewContainer.classList.remove('hidden');
                    
                    if (statusBadge) statusBadge.classList.add('hidden');
                    if (loadingText) loadingText.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
            
            ocrOutput.classList.add('hidden');
            
            // Abort previous running controller if any
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
            }
            answerOcrAbortController = new AbortController();
            const signal = answerOcrAbortController.signal;
            
            const engineSelect = document.getElementById('answerOcrEngine');
            const engine = engineSelect ? engineSelect.value : 'default';
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            
            fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                answerOcrAbortController = null;
                
                if (data.status === 'success') {
                    showToast('OCR 识别成功并已自动填入！');
                    ocrOutput.classList.remove('hidden');
                    ocrResult.textContent = cleanMathOcrText(data.latex);
                    ocrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;
                    
                    // Automatically load OCR results into final review editor silently
                    loadToFinalReview('ocr');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // Gracefully handle manual aborts without error toast
                }
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                answerOcrAbortController = null;
                showToast('OCR 识别出错: ' + err, 'error');
            });
        }

        // 2.2 OCR Question Content screenshot handler
        function triggerContentOcr() {
            document.getElementById('contentOcrFileInput').click();
        }

        function runContentOcr(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const contentOcrDropZone = document.getElementById('contentOcrDropZone');
            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            const contentOcrResult = document.getElementById('contentOcrResultText');
            const contentOcrConf = document.getElementById('contentOcrConfBadge');
            
            const previewImg = document.getElementById('contentOcrPreviewImg');
            const previewContainer = document.getElementById('contentOcrPreviewContainer');
            const placeholder = document.getElementById('contentOcrPlaceholder');
            const statusBadge = document.getElementById('contentOcrStatusBadge');
            const loadingText = document.getElementById('contentOcrStatusLoadingText');
            
            // 1. Read file to show image preview in the upload area
            const reader = new FileReader();
            reader.onload = (e) => {
                if (previewImg && previewContainer && placeholder) {
                    previewImg.src = e.target.result;
                    placeholder.classList.add('hidden');
                    previewContainer.classList.remove('hidden');
                    
                    if (statusBadge) statusBadge.classList.add('hidden');
                    if (loadingText) loadingText.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
            
            contentOcrOutput.classList.add('hidden');
            
            // Abort previous running controller if any
            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
            }
            contentOcrAbortController = new AbortController();
            const signal = contentOcrAbortController.signal;
            
            const engineSelect = document.getElementById('contentOcrEngine');
            const engine = engineSelect ? engineSelect.value : 'default';
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            
            fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                contentOcrAbortController = null;
                
                if (data.status === 'success') {
                    showToast('题干 OCR 识别成功并已自动填入！');
                    contentOcrOutput.classList.remove('hidden');
                    
                    // 1. Clean LaTeX noise, exclamation marks and leading question numbers
                    const cleanLatex = cleanMathOcrText(data.latex);
                    contentOcrResult.textContent = cleanLatex;
                    contentOcrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;
                    
                    // 2. Automatically load results into the persistent content editor
                    loadToContentEditor('ocr');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // Gracefully handle manual aborts without error toast
                }
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                contentOcrAbortController = null;
                showToast('题干 OCR 识别出错: ' + err, 'error');
            });
        }

        // Switch Question Content workflow tab - Apple Glass Style
        function switchContentTab(tabId) {
            const tabs = ['ocr', 'manual'];
            tabs.forEach(t => {
                const btn = document.getElementById(`contentTabBtn-${t}`);
                const content = document.getElementById(`contentTabContent-${t}`);

                if (t === tabId) {
                    btn.className = "glass-tab-item active flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 text-brand-600";
                    content.classList.remove('hidden');
                } else {
                    btn.className = "glass-tab-item flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 text-slate-600";
                    content.classList.add('hidden');
                }
            });
        }

        // Load content OCR result into the persistent editor textarea
        function loadToContentEditor(source, isAppend = false) {
            const textarea = document.getElementById('editContent');
            let contentToImport = '';
            
            if (source === 'ocr') {
                contentToImport = document.getElementById('contentOcrResultText').textContent;
                
                // 1. Auto-detect if it's a choice question with options A, B, C, D
                const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                
                if (hasA && hasB && hasC && hasD) {
                    const editQType = document.getElementById('editQType');
                    if (editQType) {
                        editQType.value = 'single_choice';
                        // Trigger change listener to update paper badges immediately
                        editQType.dispatchEvent(new Event('change'));
                    }
                }
                
                // 2. Automatically format the OCR content to break choice options onto separate lines beautifully
                contentToImport = formatQuestionContent(contentToImport);
            }
            
            if (!contentToImport.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            if (isAppend) {
                if (textarea.value.trim()) {
                    textarea.value += '\n' + contentToImport;
                } else {
                    textarea.value = contentToImport;
                }
            } else {
                if (textarea.value.trim()) {
                    const replace = confirm('题干编辑框中已有内容，点击"确定"将覆盖替换，点击"取消"将追加在后面。');
                    if (replace) {
                        textarea.value = contentToImport;
                    } else {
                        textarea.value += '\n' + contentToImport;
                    }
                } else {
                    textarea.value = contentToImport;
                }
            }
            
            // Refresh previews
            textarea.dispatchEvent(new Event('input'));
            showToast('已载入至题干编辑框！');
        }

        // Clear OCR image preview and OCR result box
        function clearContentOcrPreview() {
            const previewContainer = document.getElementById('contentOcrPreviewContainer');
            const placeholder = document.getElementById('contentOcrPlaceholder');
            const previewImg = document.getElementById('contentOcrPreviewImg');
            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            
            if (previewContainer && placeholder && previewImg && contentOcrOutput) {
                previewImg.src = '';
                previewContainer.classList.add('hidden');
                placeholder.classList.remove('hidden');
                contentOcrOutput.classList.add('hidden');
            }
        }

        // Clear Answer OCR image preview and result box
        function clearOcrPreview() {
            const previewContainer = document.getElementById('ocrPreviewContainer');
            const placeholder = document.getElementById('ocrPlaceholder');
            const previewImg = document.getElementById('ocrPreviewImg');
            const ocrOutput = document.getElementById('ocrOutputBox');
            
            if (previewContainer && placeholder && previewImg && ocrOutput) {
                previewImg.src = '';
                previewContainer.classList.add('hidden');
                placeholder.classList.remove('hidden');
                ocrOutput.classList.add('hidden');
            }
        }

        // Lightbox Zoom Functions
        function zoomImage(src) {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                lightboxImg.src = src;
                lightbox.classList.remove('hidden');
                // Force reflow for transitions
                lightbox.offsetHeight;
                lightbox.classList.remove('opacity-0');
                lightboxImg.classList.remove('scale-95');
                lightboxImg.classList.add('scale-100');
            }
        }

        function closeLightbox() {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                lightbox.classList.add('opacity-0');
                lightboxImg.classList.remove('scale-100');
                lightboxImg.classList.add('scale-95');
                setTimeout(() => {
                    lightbox.classList.add('hidden');
                    lightboxImg.src = '';
                }, 300);
            }
        }

        // Quick math inserting helper
        function insertContentHelper(code) {
            const textarea = document.getElementById('editContent');
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + code + originalVal.substring(endPos);
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            const newCursorPos = startPos + code.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        // Toggle thinking style micro-interactions
        function toggleThinkingStyle() {
            const toggle = document.getElementById('aiThinkingToggle');
            const icon = document.getElementById('thinkingIcon');
            const label = document.getElementById('thinkingLabel');
            if (toggle.checked) {
                icon.className = "fa-solid fa-brain text-brand-500 animate-pulse";
                label.textContent = "深度思考";
            } else {
                icon.className = "fa-solid fa-bolt text-amber-500";
                label.textContent = "极速解答";
            }
        }

        // 3. AI Intelligent Solve handler
        function triggerAISolve() {
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const customPrompt = document.getElementById('aiCustomPrompt').value;
            
            // Premium model selection & thinking toggle
            const model = document.getElementById('aiModelSelect').value;
            const thinkingToggle = document.getElementById('aiThinkingToggle');
            const thinking = thinkingToggle.checked ? 'enabled' : 'disabled';
            
            if (!content.trim()) {
                showToast('请先在上方输入题干内容，AI需要读取题干生成解答步骤！', 'error');
                return;
            }
            
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const outputBox = document.getElementById('aiOutputBox');
            const resultBox = document.getElementById('aiResultText');
            const loadingText = document.getElementById('aiLoadingText');
            
            // Set dynamic loading explanation depending on thinking mode and model
            let modelFriendly = '';
            if (model === 'deepseek-v4-pro') {
                modelFriendly = 'DeepSeek-V4-Pro (R1 满血版)';
            } else if (model === 'deepseek-v4-flash') {
                modelFriendly = 'DeepSeek-V4-Flash (R1 预览版)';
            } else if (model === 'qwen3.7-max') {
                modelFriendly = 'Ali Bailian Qwen3.7-Max (阿里旗舰大模型)';
            } else {
                modelFriendly = model;
            }

            if (thinking === 'enabled') {
                loadingText.textContent = `${modelFriendly} 正在进行深度思考、构建 LaTeX 解析步骤... (思考与生成可能需要 15-90 秒，请耐心等待)`;
            } else {
                loadingText.textContent = `${modelFriendly} 正在极速生成简要 LaTeX 解析步骤... (预计 3-10 秒即可完成，请稍后)`;
            }
            
            btn.disabled = true;
            btn.classList.add('opacity-50', 'pointer-events-none');
            loader.classList.remove('hidden');
            outputBox.classList.add('hidden');
            
            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', qtype);
            formData.append('custom_prompt', customPrompt);
            formData.append('thinking', thinking);
            formData.append('model', model);
            
            fetch('/api/ai/solve', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                btn.disabled = false;
                btn.classList.remove('opacity-50', 'pointer-events-none');
                loader.classList.add('hidden');
                
                if (data.status === 'success') {
                    showToast('AI 解析生成成功！');
                    outputBox.classList.remove('hidden');
                    resultBox.textContent = data.solution;
                    
                    // Automatically load AI solution into the final review editor
                    loadToFinalReview('ai');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.classList.remove('opacity-50', 'pointer-events-none');
                loader.classList.add('hidden');
                showToast('AI 生成解析出错: ' + err, 'error');
            });
        }

        // Import Tab results into persistent Final Review Textbox
        function loadToFinalReview(source) {
            const finalEdit = document.getElementById('editAnswerMarkdown');
            let contentToImport = '';
            
            if (source === 'ai') {
                contentToImport = document.getElementById('aiResultText').textContent;
            } else if (source === 'ocr') {
                contentToImport = document.getElementById('ocrResultText').textContent;
            }
            
            // Clean up LaTeX spacing, formula noise and leading question numbers
            if (source === 'ai' || source === 'ocr') {
                contentToImport = cleanMathOcrText(contentToImport);
            }
            
            if (!contentToImport.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            // Ask user whether to replace or append if there is already content
            if (finalEdit.value.trim()) {
                const replace = confirm('终审编辑框中已有内容，点击"确定"将替换已有内容，点击"取消"将追加在后面。');
                if (replace) {
                    finalEdit.value = contentToImport;
                } else {
                    finalEdit.value += '\n\n' + contentToImport;
                }
            } else {
                finalEdit.value = contentToImport;
            }
            
            // Refresh preview
            finalEdit.dispatchEvent(new Event('input'));
            showToast('已成功载入至终审编辑框！');
        }

        // Clear Draft
