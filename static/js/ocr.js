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
            setupPasteListener(illDropZone, (file) => uploadIllustration(file));
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

            // Initialize and persist state of skipTikz checkboxes in localStorage
            const contentSkipBox = document.getElementById('contentOcrSkipTikz');
            const answerSkipBox = document.getElementById('answerOcrSkipTikz');

            if (contentSkipBox) {
                const savedContentState = localStorage.getItem('contentOcrSkipTikz');
                if (savedContentState !== null) {
                    contentSkipBox.checked = (savedContentState === 'true');
                }
                contentSkipBox.addEventListener('change', () => {
                    localStorage.setItem('contentOcrSkipTikz', contentSkipBox.checked);
                });
            }

            if (answerSkipBox) {
                const savedAnswerState = localStorage.getItem('answerOcrSkipTikz');
                if (savedAnswerState !== null) {
                    answerSkipBox.checked = (savedAnswerState === 'true');
                }
                answerSkipBox.addEventListener('change', () => {
                    localStorage.setItem('answerOcrSkipTikz', answerSkipBox.checked);
                });
            }

            // Global smart clipboard paste routing for images/screenshots (Euclidean distance matching to closest visible DropZone)
            window.addEventListener('paste', (e) => {
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
                    // Define all potential target zones with their corresponding elements and handlers
                    const targets = [
                        {
                            element: document.getElementById('illustrationDropZone'),
                            handler: (file) => uploadIllustration(file)
                        },
                        {
                            element: document.getElementById('contentOcrDropZone'),
                            handler: (file) => runContentOcr(file)
                        },
                        {
                            element: document.getElementById('ocrDropZone'),
                            handler: (file) => runOcr(file)
                        },
                        {
                            element: document.getElementById('imageAnswerDropZone'),
                            handler: (file) => uploadAnswerImage(file)
                        }
                    ];
                    
                    // Filter to only get elements that are actually visible on screen
                    const visibleTargets = targets.filter(t => {
                        return t.element && t.element.offsetParent !== null;
                    });
                    
                    if (visibleTargets.length > 0) {
                        // Calculate coordinates of the center of the viewport
                        const viewCenterX = window.innerWidth / 2;
                        const viewCenterY = window.innerHeight / 2;
                        
                        let bestTarget = null;
                        let minDistance = Infinity;
                        
                        visibleTargets.forEach(t => {
                            const rect = t.element.getBoundingClientRect();
                            const centerX = rect.left + rect.width / 2;
                            const centerY = rect.top + rect.height / 2;
                            
                            // Euclidean distance to viewport center
                            const dx = centerX - viewCenterX;
                            const dy = centerY - viewCenterY;
                            const dist = Math.sqrt(dx * dx + dy * dy);
                            
                            if (dist < minDistance) {
                                minDistance = dist;
                                bestTarget = t;
                            }
                        });
                        
                        if (bestTarget) {
                            bestTarget.handler(imageFile);
                            e.preventDefault();
                            return;
                        }
                    }
                    
                    // Fallback to upload as illustration if no targets are visible
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
             // Protect markdown image starting indicator ![, replace other exclamation marks, and restore
             cleaned = cleaned.replace(/!\[/g, '___MARKDOWN_IMG_START___');
             cleaned = cleaned.replace(/[!！]/g, '');
             cleaned = cleaned.replace(/___MARKDOWN_IMG_START___/g, '![');
            
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

        let aiSolveRequestSequence = 0;
        let aiSolveCompletionTimer = null;

        function isAiSolveRequestCurrent(sequence, controller, editorSnapshot) {
            return sequence === aiSolveRequestSequence &&
                controller === aiSolveAbortController &&
                !controller.signal.aborted &&
                EditorState.isCurrent(editorSnapshot);
        }

        function resetAiSolveUi(resetProgress = false) {
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('opacity-50', 'pointer-events-none');
            }
            if (loader) loader.classList.add('hidden');
            if (resetProgress && progressBar) progressBar.style.width = '0%';
        }

        function abortActiveAiSolve() {
            const controller = aiSolveAbortController;
            const hadActiveRequest = Boolean(controller || aiSolveCompletionTimer);
            aiSolveRequestSequence += 1;
            aiSolveAbortController = null;
            if (aiSolveCompletionTimer) {
                clearTimeout(aiSolveCompletionTimer);
                aiSolveCompletionTimer = null;
            }
            if (controller) controller.abort();
            if (hadActiveRequest) resetAiSolveUi(true);
            return hadActiveRequest;
        }

        // EditorState calls this one boundary whenever a question, draft or new
        // editor session takes ownership of the form.
        window.invalidateEditorSessionAsyncWork = () => {
            cancelAllOcr(false);
            if (window.SingleOcrFigures) window.SingleOcrFigures.reset();
        };

        // Cancel and abort all active OCR processes (both content and answer OCR)
        function cancelAllOcr(clearWhenIdle = true) {
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

            // Handle AI solve abort
            if (abortActiveAiSolve()) {
                aborted = true;
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
            } else if (clearWhenIdle) {
                // If no active OCR process is running, clear all image previews and results completely
                clearContentOcrPreview();
                clearOcrPreview();
                showToast('已清除当前识图状态与图片', 'info');
            }
        }

        // Global Esc key listener for canceling OCR & closing lightbox
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Esc') {
                const cropModal = document.getElementById('singleOcrCropModal');
                if (cropModal) {
                    e.preventDefault();
                    const close = document.getElementById('singleOcrCropClose');
                    if (close) close.click();
                    return;
                }
                const figureLayoutPopover = document.getElementById('editorFigureLayoutPopover');
                if (figureLayoutPopover) {
                    e.preventDefault();
                    const returnFocus = figureLayoutPopover._returnFocus;
                    figureLayoutPopover.remove();
                    if (returnFocus && typeof returnFocus.focus === 'function') {
                        returnFocus.focus({ preventScroll: true });
                    }
                    return;
                }
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
            const editorSession = EditorState.snapshot();
            
            showToast('正在上传插图...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
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
                if (!EditorState.isCurrent(editorSession)) return;
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

        const EDITOR_FIGURE_SIZE_LABELS = {
            auto: '自动',
            small: '小',
            medium: '中',
            large: '大'
        };
        const EDITOR_FIGURE_ALIGN_LABELS = {
            right: '题干右侧',
            bottom_left: '下方居左',
            center: '下方居中',
            bottom_right: '下方居右'
        };

        function currentEditorFigureLayout() {
            const snapshot = window.FigureLayoutState && typeof window.FigureLayoutState.snapshot === 'function'
                ? window.FigureLayoutState.snapshot()
                : {};
            const align = ['right', 'bottom_left', 'center', 'bottom_right'].includes(snapshot.figure_align)
                ? snapshot.figure_align
                : 'right';
            const size = ['auto', 'small', 'medium', 'large'].includes(snapshot.figure_size)
                ? snapshot.figure_size
                : 'auto';
            return { align, size };
        }

        function editorFigureDimensions(size, align, count) {
            if (align === 'right') {
                return count > 1
                    ? { maxWidth: 125, maxHeight: 115 }
                    : { maxWidth: 155, maxHeight: 135 };
            }
            if (size === 'medium') return { maxWidth: 320, maxHeight: 240 };
            if (size === 'large') return { maxWidth: 420, maxHeight: 300 };
            if (size === 'auto' && count > 1) return { maxWidth: 150, maxHeight: 140 };
            return { maxWidth: 200, maxHeight: 170 };
        }

        function hasDetachedEditorFigureGroup(sourceText) {
            const source = String(sourceText || '');
            if (window.ImageLayoutTools) return Boolean(window.ImageLayoutTools.split(source).tail);
            const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
            const matches = [...source.matchAll(imagePattern)];
            if (matches.length === 0) return false;
            const firstImageIndex = matches[0].index || 0;
            return source.slice(firstImageIndex).replace(imagePattern, '').trim().length === 0;
        }

        function applyEditorFigureLayoutPreview(targetContainer = null, sourceText = null) {
            const textarea = document.getElementById('editContent');
            if (!textarea && sourceText === null) return;
            const source = String(sourceText === null ? (textarea && textarea.value || '') : sourceText);
            const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
            const matches = [...source.matchAll(imagePattern)];
            const parts = window.ImageLayoutTools ? window.ImageLayoutTools.split(source) : null;
            const detachedMatches = parts ? matches.filter(match => match.index >= parts.tailStart) : matches;
            const containersForAnchors = targetContainer ? [targetContainer]
                : ['contentPreview', 'paperContent'].map(id => document.getElementById(id));
            if (parts) containersForAnchors.forEach(container => {
                if (!container) return;
                const images = Array.from(container.querySelectorAll('img'));
                images.forEach((image, index) => {
                    const match = matches[index];
                    if (!match || match.index >= parts.tailStart) return;
                    image.dataset.editorFigureLayout = 'true';
                    image.dataset.editorImageKey = window.ImageLayoutTools.key(match[1]);
                    image.setAttribute('role', 'button');
                    image.setAttribute('tabindex', '0');
                    image.setAttribute('aria-label', '调整此图：当前位置对齐与尺寸');
                    image.setAttribute('title', '点击调整此图；按住 Cmd/Ctrl 点击查看原图');
                    image.classList.remove('cursor-zoom-in');
                    image.classList.add('cursor-pointer');
                });
            });
            if (!hasDetachedEditorFigureGroup(source)) return;

            // Keep table/interleaved images at their authored anchors, matching
            // the paper preview's existing semantic-position safeguard.
            const layout = currentEditorFigureLayout();
            const count = detachedMatches.length;
            const effectiveAlign = count > 1 && layout.align === 'right' ? 'center' : layout.align;
            const baseDimensions = editorFigureDimensions(layout.size, effectiveAlign, count);

            const containers = targetContainer
                ? [targetContainer]
                : ['contentPreview', 'paperContent'].map(containerId => document.getElementById(containerId));
            containers.forEach(container => {
                if (!container) return;
                const images = Array.from(container.querySelectorAll('img')).slice(-count);
                if (effectiveAlign === 'right' && images.length === 1) {
                    const rightWrapper = images[0].parentElement;
                    if (rightWrapper) {
                        rightWrapper.style.float = 'right';
                        rightWrapper.style.margin = '0 0 0.5rem 0.75rem';
                        container.insertBefore(rightWrapper, container.firstChild);
                        container.style.display = 'flow-root';
                    }
                } else {
                    images.forEach(image => {
                        if (image.parentElement) container.appendChild(image.parentElement);
                    });
                    container.style.display = '';
                }
                images.forEach(image => {
                    image.dataset.editorFigureLayout = 'true';
                    image.dataset.editorImageKey = '';
                    image.setAttribute('role', 'button');
                    image.setAttribute('tabindex', '0');
                    image.setAttribute(
                        'aria-label',
                        `调整插图排版：${EDITOR_FIGURE_ALIGN_LABELS[layout.align]}，${EDITOR_FIGURE_SIZE_LABELS[layout.size]}`
                    );
                    image.setAttribute('title', '点击调整插图排版；按住 Cmd/Ctrl 点击查看原图');
                    image.classList.remove('cursor-zoom-in');
                    image.classList.add('cursor-pointer');
                    const wrapper = image.parentElement;
                    if (wrapper) {
                        wrapper.style.textAlign = effectiveAlign === 'center'
                            ? 'center'
                            : (effectiveAlign === 'bottom_left' ? 'left' : 'right');
                        wrapper.style.maxWidth = '100%';
                        if (effectiveAlign !== 'right') {
                            wrapper.style.float = 'none';
                            wrapper.style.marginLeft = '0';
                        }
                    }
                    image.style.width = 'auto';
                    image.style.height = 'auto';
                    image.style.maxWidth = `min(100%, ${baseDimensions.maxWidth}px)`;
                    image.style.maxHeight = `${baseDimensions.maxHeight}px`;

                    if (layout.size !== 'auto' || effectiveAlign === 'right' || count !== 1) return;
                    const resolveAuto = () => {
                        if (!(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return;
                        const wide = image.naturalWidth / image.naturalHeight >= 1.6;
                        image.style.maxWidth = `min(100%, ${wide ? 420 : 200}px)`;
                        image.style.maxHeight = `${wide ? 300 : 170}px`;
                    };
                    if (image.complete) resolveAuto();
                    else image.addEventListener('load', resolveAuto, { once: true });
                });
            });
        }
        window.applyEditorFigureLayoutPreview = applyEditorFigureLayoutPreview;

        window.setEditorFigureLayout = function(kind, value) {
            if (!window.FigureLayoutState) return;
            const activePopover = document.getElementById('editorFigureLayoutPopover');
            const imageKey = activePopover && activePopover.dataset.imageKey;
            const contentElement = document.getElementById('editContent');
            if (activePopover && contentElement && activePopover._sourceText !== contentElement.value) {
                activePopover.remove();
                showToast('题干已变化，请重新点击要调整的图片。', 'info');
                return;
            }
            if (imageKey) {
                const existingLayout = window.FigureLayoutState.imageLayouts[imageKey] || { align: 'center', size: 'auto' };
                const next = { ...existingLayout };
                if (kind === 'align' && ['left', 'center', 'right'].includes(value)) next.align = value;
                if (kind === 'size' && ['auto', 'small', 'medium', 'large'].includes(value)) next.size = value;
                window.FigureLayoutState.imageLayouts = window.ImageLayoutTools.normalize({
                    ...window.FigureLayoutState.imageLayouts, [imageKey]: next
                });
                activePopover.remove();
                renderIllustrationBadges();
                if (contentElement) contentElement.dispatchEvent(new Event('input'));
                return;
            }
            if (kind === 'align') {
                window.FigureLayoutState.setAlign(value);
                window.FigureLayoutState.setCustomAlign(true);
                if (value === 'right' && ['medium', 'large'].includes(window.FigureLayoutState.size)) {
                    window.FigureLayoutState.setSize('small');
                }
            }
            if (kind === 'size') {
                window.FigureLayoutState.setSize(value);
                if (['medium', 'large'].includes(value)
                        && window.FigureLayoutState.align === 'right') {
                    window.FigureLayoutState.setAlign('bottom_right');
                    window.FigureLayoutState.setCustomAlign(true);
                }
            }

            const popover = document.getElementById('editorFigureLayoutPopover');
            if (popover) popover.remove();
            if (typeof window.renderIllustrationBadges === 'function') {
                window.renderIllustrationBadges();
            }
            const textarea = document.getElementById('editContent');
            if (textarea) textarea.dispatchEvent(new Event('input'));
        };

        window.showEditorFigureLayoutPopover = function(event) {
            event.preventDefault();
            event.stopPropagation();
            const existing = document.getElementById('editorFigureLayoutPopover');
            if (existing) existing.remove();

            const selected = event.target && event.target.closest ? event.target.closest('[data-editor-image-key]') : null;
            const imageKey = selected && selected.dataset.editorImageKey || '';
            const layout = imageKey ? (window.FigureLayoutState.imageLayouts[imageKey] || { align: 'center', size: 'auto' }) : currentEditorFigureLayout();
            const popover = document.createElement('div');
            popover.dataset.imageKey = imageKey;
            popover._sourceText = document.getElementById('editContent').value;
            popover.id = 'editorFigureLayoutPopover';
            popover.setAttribute('role', 'dialog');
            popover.setAttribute('aria-label', '调整插图排版');
            popover.className = 'fixed z-50 w-64 rounded-2xl border border-slate-200 bg-white/95 p-2 font-sans text-xs text-slate-700 shadow-xl backdrop-blur-md dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100';
            const anchor = event.target && event.target.closest
                ? event.target.closest('img[data-editor-figure-layout]')
                : null;
            const anchorRect = anchor ? anchor.getBoundingClientRect() : null;
            popover._returnFocus = anchor || document.activeElement;
            let left = event.clientX > 0
                ? event.clientX + 5
                : (anchorRect ? anchorRect.right + 5 : 8);
            let top = event.clientY > 0
                ? event.clientY + 5
                : (anchorRect ? anchorRect.top : 8);
            if (left + 256 > window.innerWidth) left = window.innerWidth - 266;
            if (top + 238 > window.innerHeight) top = window.innerHeight - 248;
            popover.style.left = `${Math.max(8, left)}px`;
            popover.style.top = `${Math.max(8, top)}px`;

            const alignLabels = imageKey ? { left: '当前位置居左', center: '当前位置居中', right: '当前位置居右' } : EDITOR_FIGURE_ALIGN_LABELS;
            const alignButtons = Object.keys(alignLabels).map(align => `
                <button type="button" onclick="window.setEditorFigureLayout('align', '${align}')"
                    aria-label="插图位置：${alignLabels[align]}"
                    aria-pressed="${layout.align === align ? 'true' : 'false'}"
                    class="min-w-0 rounded-md border px-2 py-1.5 text-[10px] ${layout.align === align ? 'border-brand-200 bg-brand-50 font-bold text-brand-700' : 'border-slate-200 text-slate-500 hover:border-brand-200 hover:text-brand-600 dark:border-slate-600 dark:text-slate-300'}">${alignLabels[align]}</button>
            `).join('');
            const sizeButtons = ['auto', 'small', 'medium', 'large'].map(size => `
                <button type="button" onclick="window.setEditorFigureLayout('size', '${size}')"
                    aria-pressed="${layout.size === size ? 'true' : 'false'}"
                    class="min-w-0 flex-1 rounded-md border px-1 py-1 text-[10px] ${layout.size === size ? 'border-brand-200 bg-brand-50 font-bold text-brand-700' : 'border-slate-200 text-slate-500 hover:border-brand-200 hover:text-brand-600 dark:border-slate-600 dark:text-slate-300'}">${EDITOR_FIGURE_SIZE_LABELS[size]}</button>
            `).join('');
            popover.innerHTML = `
                <div class="mb-2 flex items-center justify-between border-b border-slate-100 px-1 pb-1.5 font-bold dark:border-slate-700">
                    <span><i class="fa-solid fa-sliders mr-1 text-brand-500"></i>${imageKey ? '此图排版' : '题末图片组排版'}</span>
                    <button type="button" onclick="document.getElementById('editorFigureLayoutPopover').remove()" aria-label="关闭插图排版" class="text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="mb-2">
                    <div class="mb-1 text-[10px] text-slate-400">位置</div>
                    <div class="grid grid-cols-2 gap-1">${alignButtons}</div>
                </div>
                <div>
                    <div class="mb-1 flex justify-between text-[10px] text-slate-400"><span>尺寸</span><span>${imageKey ? '保持正文顺序' : (layout.align === 'right' ? '中/大图自动改为下方居右' : '下方布局生效')}</span></div>
                    <div class="flex gap-1">${sizeButtons}</div>
                </div>
                <div class="mt-2 text-[9px] text-slate-400">随题目保存后写入题库</div>
            `;
            document.body.appendChild(popover);
            popover.style.maxHeight = 'calc(100vh - 16px)';
            popover.style.overflowY = 'auto';
            const bounds = popover.getBoundingClientRect();
            popover.style.top = `${Math.max(8, Math.min(top, window.innerHeight - bounds.height - 8))}px`;
            popover.style.left = `${Math.max(8, Math.min(left, window.innerWidth - bounds.width - 8))}px`;

            const closeHandler = function(closeEvent) {
                if (!popover.contains(closeEvent.target)) {
                    popover.remove();
                    document.removeEventListener('click', closeHandler);
                }
            };
            const removeElement = popover.remove.bind(popover);
            popover.remove = () => {
                document.removeEventListener('click', closeHandler);
                if (window.MathBankModal) window.MathBankModal.close(popover);
                removeElement();
            };
            if (window.MathBankModal) {
                if (anchor) anchor.focus({ preventScroll: true });
                window.MathBankModal.open(popover, { onEscape: () => popover.remove() });
            }
            setTimeout(() => {
                if (popover.isConnected) document.addEventListener('click', closeHandler);
            }, 0);
        };

        function handleEditorFigureLayoutPreviewClick(event) {
            const image = event.target && event.target.closest
                ? event.target.closest('img[data-editor-figure-layout]')
                : null;
            if (!image || event.metaKey || event.ctrlKey) return;
            event.preventDefault();
            event.stopPropagation();
            window.showEditorFigureLayoutPopover(event);
        }

        function handleEditorFigureLayoutPreviewKeydown(event) {
            const image = event.target && event.target.closest
                ? event.target.closest('img[data-editor-figure-layout]')
                : null;
            if (!image || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            event.stopPropagation();
            window.showEditorFigureLayoutPopover(event);
        }

        // Capture before api.js's generic image opener so a normal click edits
        // layout. Cmd/Ctrl-click intentionally falls through to view the source.
        document.addEventListener('click', handleEditorFigureLayoutPreviewClick, true);
        document.addEventListener('keydown', handleEditorFigureLayoutPreviewKeydown, true);

        function renderIllustrationBadges() {
            const listContainer = document.getElementById('illustrationsList');
            if (!listContainer) return;
            listContainer.innerHTML = '';

            const hiddenReferencePaths = new Set(TikzState.referencePaths());
            let layoutChipRendered = false;
            const textarea = document.getElementById('editContent');
            const allowLayoutControls = hasDetachedEditorFigureGroup(textarea ? textarea.value : '');
            const parts = window.ImageLayoutTools.split(textarea ? textarea.value : '');
            const anchoredKeys = new Set(parts.matches.filter(m => m.index < parts.tailStart).map(m => window.ImageLayoutTools.key(m[1])));

            uploadedImages.forEach((path, idx) => {
                if (hiddenReferencePaths.has(path)) return;
                const filename = path.split('/').pop();
                const safeFilename = window.MathBankSafe.escapeText(filename);
                const safeFilenameAttr = window.MathBankSafe.escapeAttribute(filename);
                const imageKey = window.ImageLayoutTools.key(path);
                const anchored = anchoredKeys.has(imageKey);
                const layout = anchored ? (window.FigureLayoutState.imageLayouts[imageKey] || { align: 'center', size: 'auto' }) : currentEditorFigureLayout();
                const layoutLabel = `${EDITOR_FIGURE_ALIGN_LABELS[layout.align]} · ${EDITOR_FIGURE_SIZE_LABELS[layout.size]}`;
                const showLayoutChip = anchored || (allowLayoutControls && !layoutChipRendered);
                if (showLayoutChip && !anchored) layoutChipRendered = true;
                const fileDisplay = `<button type="button" data-editor-image-key="${window.MathBankSafe.escapeAttribute(anchored ? imageKey : '')}" onclick="window.showEditorFigureLayoutPopover(event)" class="flex min-w-0 items-center gap-1.5 text-left hover:text-brand-600" title="${anchored ? '调整此图的对齐与尺寸，保持正文位置' : '调整题末图片组'}">
                    <i class="fa-solid fa-file-image shrink-0 text-brand-500"></i>
                    <span class="truncate max-w-[100px]" title="${safeFilenameAttr}">${safeFilename}</span>
                    ${showLayoutChip ? `<span class="shrink-0 rounded bg-brand-50 px-1 py-0.5 text-[9px] text-brand-600">${anchored ? '正文 · ' : ''}${EDITOR_FIGURE_SIZE_LABELS[layout.size]}</span>` : ''}
                </button>`;
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        ${fileDisplay}
                        <button type="button" onclick="deleteUploadedIllustration(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
            if (typeof window.renderContentTikzAssets === 'function') {
                window.renderContentTikzAssets();
            }
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
            const editorSession = EditorState.snapshot();
            
            showToast('正在上传图片解答...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
                if (data.status === 'success') {
                    showToast('图片解答上传成功！');
                    insertAnswerImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (!EditorState.isCurrent(editorSession)) return;
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
                if (val === 'deepseek') {
                    return "DeepSeek";
                } else if (val === 'siliconflow') {
                    return "SiliconFlow 硅基流动云端";
                } else if (val === 'ali_bailian') {
                    return "阿里百炼";
                }
                return val || "";
            };

            const label = `当前引擎: ${getEngineLabel(systemPreferEngine)}`;

            if (type === 'content') {
                const subText = document.getElementById('contentOcrPlaceholderSub');
                if (subText) {
                    subText.textContent = label;
                }
            } else if (type === 'answer') {
                const subText = document.getElementById('answerOcrPlaceholderSub');
                if (subText) {
                    subText.textContent = label;
                }
            }
        }

        // A single scoped owner keeps OCR sources, figure slots and crop work
        // tied to the editor session; visible images remain owned by Markdown.
        const SingleOcrFigures = (() => {
            const results = { content: null, answer: null };
            let crop = null;
            const config = target => target === 'content' ? {
                editor: 'editContent', result: 'contentOcrResultText', output: 'contentOcrOutputBox',
                figures: 'contentOcrFigures', preview: 'contentOcrPreviewImg', container: 'contentOcrPreviewContainer',
                placeholder: 'contentOcrPlaceholder', status: 'contentOcrStatusBadge', loading: 'contentOcrStatusLoadingText',
                confidence: 'contentOcrConfBadge', skip: 'contentOcrSkipTikz'
            } : {
                editor: 'editAnswerMarkdown', result: 'ocrResultText', output: 'ocrOutputBox',
                figures: 'answerOcrFigures', preview: 'ocrPreviewImg', container: 'ocrPreviewContainer',
                placeholder: 'ocrPlaceholder', status: 'ocrStatusBadge', loading: 'ocrStatusLoadingText',
                confidence: 'ocrConfBadge', skip: 'answerOcrSkipTikz'
            };
            const element = (target, key) => document.getElementById(config(target)[key]);
            const current = state => state && results[state.target] === state && EditorState.isCurrent(state.session);
            function visibleSlotMatches(text, token = null) {
                const value = String(text || '');
                // Equal-length masks retain source offsets for exact insertion.
                // Reuse the preview scanners so code and math cannot own a slot.
                const mask = whole => whole.replace(/[^\r\n]/g, ' ');
                let source = value.replace(/<!--[\s\S]*?(?:-->|$)|!?\[[^\[\]\n]*\]\([^\n)]*\)/g, mask);
                source = replacePreviewLiteralSpans(source, mask);
                source = replaceDelimitedMathForPreview(source, mask);
                source = replaceLatexEnvironmentsForPreview(source, (whole, name) =>
                    /^(tabular\*?|tabularx|longtable|tblr|longtblr|talltblr)$/.test(name) ? whole : mask(whole));
                const matches = [...source.matchAll(/\[插图待补(?:\s*[：:]\s*([^\]\n]{0,80}))?\]/g)];
                return token === null ? matches : matches.filter(match => match[0] === token);
            }
            const occurrences = (text, token) => token ? visibleSlotMatches(text, token).length : 0;
            function closeCrop() {
                if (crop && crop.controller) crop.controller.abort();
                crop = null;
                const modal = document.getElementById('singleOcrCropModal');
                if (modal) {
                    if (window.MathBankModal) window.MathBankModal.close(modal);
                    modal.remove();
                }
            }
            function reset(target) {
                if (crop && (!target || crop.state.target === target)) closeCrop();
                (target ? [target] : ['content', 'answer']).forEach(name => {
                    results[name] = null;
                    const panel = element(name, 'figures');
                    if (panel) { panel.replaceChildren(); panel.classList.add('hidden'); }
                });
            }
            function begin(target) {
                reset(target);
                const state = { target, session: EditorState.snapshot(), original: element(target, 'editor').value,
                    data: null, applied: false, source: '', illustrations: [] };
                results[target] = state;
                return state;
            }
            function button(label, action) {
                const node = document.createElement('button');
                node.type = 'button';
                node.className = 'inline-flex min-h-[44px] shrink-0 items-center justify-center rounded-lg border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700 hover:bg-brand-100 focus:outline-none focus:ring-2 focus:ring-brand-500/30';
                node.textContent = label;
                node.addEventListener('click', action);
                return node;
            }
            function render(state) {
                if (!current(state)) return;
                const panel = element(state.target, 'figures');
                if (!panel) return;
                panel.replaceChildren();
                panel.classList.remove('hidden');
                const intro = document.createElement('p');
                intro.className = 'text-xs leading-5 text-slate-500';
                intro.textContent = state.applied
                    ? '配图已按识别图位处理。点击预览中的题干图片可调整对齐与尺寸；未定位的插图仍可从原图截图补入。'
                    : '识别期间编辑内容发生变化，结果已保留。载入结果后可按图位截图补图。';
                panel.appendChild(intro);
                if (!state.applied) panel.appendChild(button('载入识别结果', () => apply(state)));
                const text = element(state.target, 'editor').value;
                const visiblePaths = new Set(window.MathBankImageAssets ? window.MathBankImageAssets.collect(text) : []);
                state.illustrations.forEach((figure, index) => {
                    const row = document.createElement('div');
                    row.className = 'flex flex-wrap items-center justify-between gap-2 rounded-lg bg-slate-50 px-2.5 py-2';
                    const label = document.createElement('span');
                    label.className = 'min-w-0 text-xs text-slate-600';
                    const unresolved = state.applied && occurrences(text, figure.placeholder) === 1;
                    const imagePath = window.MathBankSafe.safeImageUrl(figure.image_path);
                    const visible = state.applied && imagePath && visiblePaths.has(imagePath);
                    let status = !state.applied ? '待载入' : unresolved ? '待截图'
                        : visible ? figure.manual_crop ? '手动截图' : figure.status === 'tikz' ? 'TikZ 绘图' : '原图截图'
                        : imagePath ? '图位已变化' : '待核对图位';
                    const review = figure.status === 'tikz' ? figure.preview_review : figure.review;
                    if (visible && !figure.manual_crop && review && review.status === 'verified') {
                        status += figure.status === 'tikz' ? ' · 绘图已视觉核对'
                            : review.corrected === true ? ' · 已修正裁图并核对' : ' · 原图裁片已视觉核对';
                    } else if (unresolved && figure.review) {
                        if (figure.review.status === 'unverified') status += ' · 视觉核对未通过';
                        if (figure.review.status === 'unavailable') status += ' · 视觉核对暂不可用';
                    }
                    label.textContent = `${figure.slot || '插图 ' + (index + 1)} · ${status}`;
                    const evidence = review && typeof review.evidence === 'string' ? review.evidence : '';
                    if (figure.message || evidence) label.title = [figure.message, evidence].filter(Boolean).join('\n');
                    row.appendChild(label);
                    if (unresolved) row.appendChild(button('截图补入此处', () => openCrop(state, figure)));
                    panel.appendChild(row);
                });
                if (state.applied && state.source) panel.appendChild(button('从原图追加截图', () => openCrop(state, null)));
                (Array.isArray(state.data && state.data.warnings) ? state.data.warnings : []).forEach(warning => {
                    const note = document.createElement('p');
                    note.className = 'text-[11px] leading-5 text-amber-700';
                    note.textContent = String(warning);
                    panel.appendChild(note);
                });
            }
            function registerAssets(state) {
                const text = element(state.target, 'editor').value;
                const payload = state.data || {};
                const assets = Array.isArray(payload.tikz_assets) ? payload.tikz_assets : payload.tikz_code && payload.tikz_image_path ? [{
                    tikz_code: payload.tikz_code, image_path: payload.tikz_image_path, reference_image_path: payload.image_path
                }] : [];
                const register = state.target === 'content' ? window.registerAutoContentTikzAsset : window.registerAutoAnswerTikzAsset;
                assets.forEach(asset => {
                    const path = window.MathBankSafe.safeImageUrl(asset.image_path);
                    if (path && text.includes(path) && typeof register === 'function') register({
                        tikzCode: asset.tikz_code, imagePath: path, referenceImagePath: asset.reference_image_path || ''
                    });
                });
                if (state.target === 'content' && window.FigureLayoutState && window.ImageLayoutTools) {
                    const layouts = window.ImageLayoutTools.normalize(payload.image_layouts || {});
                    Object.keys(layouts).forEach(path => { if (!text.includes(path)) delete layouts[path]; });
                    window.FigureLayoutState.imageLayouts = window.ImageLayoutTools.normalize({
                        ...window.FigureLayoutState.imageLayouts, ...layouts
                    });
                }
                element(state.target, 'editor').dispatchEvent(new Event('input'));
            }
            function apply(state) {
                if (!current(state) || !state.data) return;
                if (state.target === 'content') loadToContentEditor('ocr');
                else loadToFinalReview('ocr');
                state.applied = true;
                registerAssets(state);
                render(state);
            }
            function complete(state, data) {
                if (!current(state)) return;
                state.data = data;
                state.source = window.MathBankSafe.safeImageUrl(data.image_path) || state.source;
                state.illustrations = (Array.isArray(data.illustrations) ? data.illustrations : []).filter(figure => figure && typeof figure.placeholder === 'string');
                // Partial/legacy metadata still exposes every visible pending slot.
                const slots = visibleSlotMatches(data.latex);
                slots.forEach((match, index) => {
                    if (!state.illustrations.some(figure => figure.placeholder === match[0])) {
                        state.illustrations.push({ placeholder: match[0], slot: match[1] || '图' + (index + 1), status: 'pending' });
                    }
                });
                if (element(state.target, 'editor').value === state.original) apply(state);
                else { render(state); showToast('识别结果已保留，请载入后继续编辑。', 'info'); }
            }
            function openCrop(state, figure) {
                if (!current(state) || !state.applied || !state.source) return;
                const textarea = element(state.target, 'editor');
                if (figure && occurrences(textarea.value, figure.placeholder) !== 1) {
                    showToast('图位已变化，请核对编辑框中的待补位置。', 'info');
                    return;
                }
                closeCrop();
                const selection = { state, figure, original: textarea.value,
                    insertionStart: textarea.selectionStart, insertionEnd: textarea.selectionEnd,
                    image: new Image(), box: null, start: null, ready: false, controller: null };
                crop = selection;
                const modal = document.createElement('div');
                modal.id = 'singleOcrCropModal';
                modal.setAttribute('role', 'dialog');
                modal.setAttribute('aria-modal', 'true');
                modal.setAttribute('aria-label', '从 OCR 原图截取插图');
                modal.className = 'fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/80 p-3 backdrop-blur-sm';
                // This is fixed UI markup. OCR labels and sources are assigned with DOM APIs below.
                modal.innerHTML = '<div data-modal-surface class="flex max-h-[94vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"><div class="flex items-center justify-between gap-3 border-b border-slate-200 p-4"><div><p id="singleOcrCropTitle" class="text-sm font-bold text-slate-800"></p><p class="mt-1 text-xs text-slate-500">在原图上拖动框选插图，确认后放回对应图位。</p></div><button type="button" id="singleOcrCropClose" class="min-h-[44px] rounded-lg px-3 text-sm text-slate-500" aria-label="关闭截图">关闭</button></div><div class="min-h-0 flex-1 overflow-auto bg-slate-100 p-3"><canvas id="singleOcrCropCanvas" class="mx-auto block max-w-full touch-none bg-white" style="cursor:crosshair"></canvas></div><div class="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 p-4"><p id="singleOcrCropStatus" class="text-xs text-slate-500" role="status">正在加载原图…</p><div class="flex gap-2"><button id="singleOcrCropClear" type="button" class="min-h-[44px] rounded-lg border border-slate-200 px-3 text-xs text-slate-600">重新框选</button><button id="singleOcrCropSave" type="button" disabled class="min-h-[44px] rounded-lg bg-brand-600 px-4 text-xs font-bold text-white disabled:opacity-40">确认截取配图</button></div></div></div>';
                document.body.appendChild(modal);
                const canvas = document.getElementById('singleOcrCropCanvas');
                const save = document.getElementById('singleOcrCropSave');
                const status = document.getElementById('singleOcrCropStatus');
                document.getElementById('singleOcrCropTitle').textContent = figure ? '截图补入 ' + figure.slot : '追加一幅截图到光标位置';
                document.getElementById('singleOcrCropClose').onclick = closeCrop;
                if (window.MathBankModal) window.MathBankModal.open(modal, { onEscape: closeCrop });
                const draw = () => {
                    const ctx = canvas.getContext('2d');
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    ctx.drawImage(selection.image, 0, 0, canvas.width, canvas.height);
                    if (selection.box) {
                        const box = selection.box;
                        ctx.fillStyle = 'rgba(99,102,241,0.16)';
                        ctx.fillRect(box.left, box.top, box.right - box.left, box.bottom - box.top);
                        ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
                        ctx.strokeRect(box.left, box.top, box.right - box.left, box.bottom - box.top);
                    }
                    save.disabled = !selection.box || selection.box.right - selection.box.left < 4 || selection.box.bottom - selection.box.top < 4;
                };
                const point = event => {
                    const rect = canvas.getBoundingClientRect();
                    return { x: Math.max(0, Math.min(canvas.width, (event.clientX - rect.left) * canvas.width / rect.width)),
                        y: Math.max(0, Math.min(canvas.height, (event.clientY - rect.top) * canvas.height / rect.height)) };
                };
                canvas.onpointerdown = event => {
                    if (selection.controller || !selection.ready) return;
                    event.preventDefault();
                    selection.start = point(event);
                    canvas.setPointerCapture(event.pointerId);
                };
                canvas.onpointermove = event => {
                    if (!selection.start) return;
                    const end = point(event);
                    selection.box = { left: Math.min(selection.start.x, end.x), top: Math.min(selection.start.y, end.y),
                        right: Math.max(selection.start.x, end.x), bottom: Math.max(selection.start.y, end.y) };
                    draw(); status.textContent = '确认前请检查框内只包含这幅插图。';
                };
                canvas.onpointerup = canvas.onpointercancel = () => { selection.start = null; };
                document.getElementById('singleOcrCropClear').onclick = () => {
                    if (selection.controller) return;
                    selection.box = null; draw(); status.textContent = '请拖动框选插图区域。';
                };
                save.onclick = () => submitCrop(selection);
                selection.image.onload = () => {
                    if (crop !== selection || !current(state)) return;
                    const factor = Math.min(1, 1600 / Math.max(selection.image.naturalWidth, selection.image.naturalHeight));
                    canvas.width = Math.max(1, Math.round(selection.image.naturalWidth * factor));
                    canvas.height = Math.max(1, Math.round(selection.image.naturalHeight * factor));
                    // A manual repair slot is unresolved. Failed initial bounds
                    // remain diagnostics, never a ready-to-submit selection.
                    selection.ready = true;
                    draw(); status.textContent = figure ? '原图框未确认，请手动框选完整插图' : '请拖动框选插图区域。';
                };
                selection.image.onerror = () => { if (crop === selection) status.textContent = '原图加载失败，请关闭后重试。'; };
                selection.image.src = state.source;
            }
            async function submitCrop(selection) {
                const state = selection.state;
                const textarea = element(state.target, 'editor');
                if (crop !== selection || !current(state) || !selection.box || selection.controller) return;
                if (textarea.value !== selection.original) {
                    closeCrop(); showToast('编辑内容已变化，请重新打开截图，避免放错位置。', 'info'); return;
                }
                const source = document.getElementById('singleOcrCropCanvas');
                const box = selection.box;
                const scaleX = selection.image.naturalWidth / source.width;
                const scaleY = selection.image.naturalHeight / source.height;
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round((box.right - box.left) * scaleX));
                canvas.height = Math.max(1, Math.round((box.bottom - box.top) * scaleY));
                canvas.getContext('2d').drawImage(selection.image, box.left * scaleX, box.top * scaleY,
                    canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
                selection.controller = new AbortController();
                const save = document.getElementById('singleOcrCropSave');
                const status = document.getElementById('singleOcrCropStatus');
                save.disabled = true; status.textContent = '正在保存截图…';
                try {
                    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
                    if (crop !== selection || !current(state) || selection.controller.signal.aborted) return;
                    if (!blob) throw new Error('无法生成截图');
                    const form = new FormData(); form.append('file', blob, 'ocr-figure.png');
                    const response = await fetch('/api/upload', { method: 'POST', body: form, signal: selection.controller.signal });
                    const data = await response.json();
                    if (crop !== selection || !current(state) || selection.controller.signal.aborted) return;
                    if (textarea.value !== selection.original) {
                        closeCrop(); showToast('截图已上传，编辑内容已变化，请重新截图定位。', 'info'); return;
                    }
                    const path = window.MathBankSafe.safeImageUrl(data.file_path);
                    if (!response.ok || data.status !== 'success' || !path) throw new Error(data.message || '截图保存失败');
                    const label = String(selection.figure && selection.figure.slot || '插图').replace(/[\[\]\\\r\n]/g, '');
                    const tag = `![${label}](${path})`;
                    if (selection.figure) {
                        if (occurrences(textarea.value, selection.figure.placeholder) !== 1) {
                            closeCrop(); showToast('图位已变化，请重新定位。', 'info'); return;
                        }
                        const match = visibleSlotMatches(textarea.value, selection.figure.placeholder)[0];
                        textarea.value = textarea.value.slice(0, match.index) + tag
                            + textarea.value.slice(match.index + match[0].length);
                        selection.figure.image_path = path;
                        selection.figure.status = 'cropped';
                        selection.figure.manual_crop = true;
                        delete selection.figure.review;
                        delete selection.figure.preview_review;
                    } else {
                        textarea.value = textarea.value.slice(0, selection.insertionStart) + tag + textarea.value.slice(selection.insertionEnd);
                    }
                    if (state.target === 'content' && window.FigureLayoutState && window.ImageLayoutTools) {
                        // A cropped slot remains inline, including choices and table cells.
                        const key = window.ImageLayoutTools.key(path);
                        window.FigureLayoutState.imageLayouts = window.ImageLayoutTools.normalize({
                            ...window.FigureLayoutState.imageLayouts, [key]: { align: 'left', size: 'small' }
                        });
                    }
                    textarea.dispatchEvent(new Event('input'));
                    closeCrop(); render(state); showToast('截图已补入对应位置。');
                } catch (error) {
                    if (crop !== selection || !current(state) || error.name === 'AbortError') return;
                    selection.controller = null; save.disabled = false;
                    status.textContent = error.message; showToast('截图失败：' + error.message, 'error');
                }
            }
            return { begin, current, complete, reset, config };
        })();
        window.SingleOcrFigures = SingleOcrFigures;

        function runOcr(file) { runSingleOcr('answer', file); }
        function runContentOcr(file) { runSingleOcr('content', file); }

        function runSingleOcr(target, file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error'); return;
            }
            const names = SingleOcrFigures.config(target);
            const get = key => document.getElementById(names[key]);
            const previous = target === 'content' ? contentOcrAbortController : answerOcrAbortController;
            if (previous) previous.abort();
            const controller = new AbortController();
            if (target === 'content') contentOcrAbortController = controller;
            else answerOcrAbortController = controller;
            const state = SingleOcrFigures.begin(target);
            const requestIsCurrent = () => SingleOcrFigures.current(state) && !controller.signal.aborted
                && controller === (target === 'content' ? contentOcrAbortController : answerOcrAbortController);
            get('output').classList.add('hidden');
            get('status').classList.add('hidden');
            get('loading').classList.remove('hidden');
            const reader = new FileReader();
            reader.onload = event => {
                if (!SingleOcrFigures.current(state) || controller.signal.aborted) return;
                if (!state.data) state.source = event.target.result;
                get('preview').src = event.target.result;
                get('placeholder').classList.add('hidden');
                get('container').classList.remove('hidden');
            };
            reader.readAsDataURL(file);
            const form = new FormData();
            form.append('file', file); form.append('engine', 'default');
            form.append('skip_tikz', get('skip') && get('skip').checked ? 'true' : 'false');
            const finish = () => {
                get('loading').classList.add('hidden');
                get('status').classList.remove('hidden');
                get('status').textContent = '已加载截图预览 (点击可更换图片)';
                if (target === 'content') contentOcrAbortController = null;
                else answerOcrAbortController = null;
            };
            fetch('/api/ocr', { method: 'POST', body: form, signal: controller.signal })
                .then(async response => {
                    const data = await response.json();
                    if (!requestIsCurrent()) return;
                    finish();
                    if (!response.ok || data.status !== 'success') { showToast(data.message || 'OCR 识别失败', 'error'); return; }
                    get('output').classList.remove('hidden');
                    get('result').textContent = cleanMathOcrText(data.latex);
                    const confidence = Number(data.confidence);
                    get('confidence').textContent = Number.isFinite(confidence) ? `置信度: ${(confidence * 100).toFixed(1)}%` : '请核对识别结果';
                    SingleOcrFigures.complete(state, data);
                })
                .catch(error => {
                    if (!requestIsCurrent() || error.name === 'AbortError') return;
                    finish(); showToast('OCR 识别出错: ' + error.message, 'error');
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
                
                if (/\\begin\s*\{choices\}/.test(contentToImport) || (hasA && hasB && hasC && hasD)) {
                    const editQType = document.getElementById('editQType');
                    if (editQType && !['single_choice', 'multi_choice'].includes(editQType.value)) {
                        editQType.value = 'single_choice';
                        // Trigger change listener to update paper badges immediately
                        editQType.dispatchEvent(new Event('change'));
                    }
                }
                
                // Canonical choices already owns option boundaries. Plain-label
                // reformatting would also rewrite A.png inside its image paths.
                if (!/\\begin\s*\{choices\}[\s\S]*?\\end\s*\{choices\}/.test(contentToImport)) {
                    contentToImport = formatQuestionContent(contentToImport);
                }
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
            if (typeof window.extractTikzCodeFromTextarea === 'function') {
                window.extractTikzCodeFromTextarea('editContent');
            }
        }

        // Clear OCR image preview and OCR result box
        function clearContentOcrPreview() {
            if (window.SingleOcrFigures) window.SingleOcrFigures.reset('content');
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
            if (window.SingleOcrFigures) window.SingleOcrFigures.reset('answer');
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
                window.MathBankModal.open(lightbox, { onEscape: closeLightbox });
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
                window.MathBankModal.close(lightbox);
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
            
            // Use globally configured preferred solve model
            const model = typeof systemPreferSolveModel !== 'undefined' ? systemPreferSolveModel : 'deepseek-v4-pro';
            const thinkingToggle = document.getElementById('aiThinkingToggle');
            const thinking = (thinkingToggle && thinkingToggle.checked) ? 'enabled' : 'disabled';
            
            if (!content.trim()) {
                showToast('请先在上方输入题干内容，AI需要读取题干生成解答步骤！', 'error');
                return;
            }
            
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const outputBox = document.getElementById('aiOutputBox');
            const resultBox = document.getElementById('aiResultText');
            const loadingText = document.getElementById('aiLoadingText');
            
            const ocrResultTextEl = document.getElementById('ocrResultText');
            const ocrResult = ocrResultTextEl ? ocrResultTextEl.textContent.trim() : '';

            // Set dynamic loading explanation depending on thinking mode and model
            let modelFriendly = model.includes('/') ? model.split('/').pop() : model;

            if (ocrResult) {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果进行深度思考并构建 LaTeX 解析... (大约需要 15-90 秒)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果极速生成 LaTeX 解析... (预计 3-10 秒)`;
                }
            } else {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在进行深度思考并构建 LaTeX 解析步骤... (思考与生成可能需要 15-90 秒，请耐心等待)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在极速生成简要 LaTeX 解析步骤... (预计 3-10 秒即可完成，请稍后)`;
                }
            }
            
            btn.disabled = true;
            btn.classList.add('opacity-50', 'pointer-events-none');
            loader.classList.remove('hidden');
            outputBox.classList.add('hidden');
            
            // Initialize progress bar
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (progressBar) {
                progressBar.style.width = '0%';
            }
            
            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', qtype);
            formData.append('ocr_result', ocrResult);
            formData.append('custom_prompt', customPrompt);
            formData.append('thinking', thinking);
            formData.append('model', model);
            formData.append('stream', 'true'); // Opt-in to real-time streaming
            
            abortActiveAiSolve();
            const editorSnapshot = EditorState.snapshot();
            const requestSequence = ++aiSolveRequestSequence;
            const requestController = new AbortController();
            aiSolveAbortController = requestController;

            const requestIsCurrent = () => isAiSolveRequestCurrent(
                requestSequence,
                requestController,
                editorSnapshot
            );

            const finishCurrentRequest = (resetProgress = false) => {
                if (!requestIsCurrent()) return false;
                aiSolveAbortController = null;
                if (aiSolveCompletionTimer) {
                    clearTimeout(aiSolveCompletionTimer);
                    aiSolveCompletionTimer = null;
                }
                resetAiSolveUi(resetProgress);
                return true;
            };
            
            fetch('/api/ai/solve', {
                method: 'POST',
                body: formData,
                signal: requestController.signal
            })
            .then(response => {
                if (!requestIsCurrent()) return;
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                if (progressBar) progressBar.style.width = '3%';
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let accumulatedSolution = '';
                let accumulatedReasoning = '';
                
                function read() {
                    return reader.read().then(({ done, value }) => {
                        if (!requestIsCurrent()) return;
                        if (done) {
                            throw new Error('AI 流式响应意外中断');
                        }
                        
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop(); // Keep last incomplete line
                        
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith('data:')) {
                                try {
                                    const eventData = JSON.parse(trimmed.slice(5).trim());
                                    if (!requestIsCurrent()) return;
                                    if (eventData.status === 'processing') {
                                        if (eventData.reasoning) {
                                            accumulatedReasoning += eventData.reasoning;
                                            const rCount = eventData.reasoning_count || 0;
                                            const rPct = Math.min(50, rCount * 0.08); // Up to 50%
                                            if (progressBar) progressBar.style.width = `${3 + rPct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在进行深度推理思考 (已生成 ${rCount} 个 Token)...`;
                                            }
                                        }
                                        if (eventData.content) {
                                            accumulatedSolution += eventData.content;
                                            const cCount = eventData.content_count || 0;
                                            const rCount = eventData.reasoning_count || 0;
                                            let pct = 3;
                                            if (rCount > 0) {
                                                const rPct = Math.min(50, rCount * 0.08);
                                                const cPct = Math.min(45, cCount * 0.05);
                                                pct += rPct + cPct;
                                            } else {
                                                pct += Math.min(92, cCount * 0.08);
                                            }
                                            if (progressBar) progressBar.style.width = `${pct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在生成 LaTeX 解析步骤 (已输出 ${cCount} 个 Token)...`;
                                            }
                                        }
                                    } else if (eventData.status === 'error') {
                                        return Promise.reject(new Error(eventData.message || 'AI 服务返回错误'));
                                    } else if (eventData.status === 'done') {
                                        if (progressBar) progressBar.style.width = '100%';
                                        aiSolveCompletionTimer = setTimeout(() => {
                                            aiSolveCompletionTimer = null;
                                            // The editor can change during the 300ms
                                            // completion animation. Recheck before
                                            // touching either result field.
                                            if (!requestIsCurrent()) return;
                                            outputBox.classList.remove('hidden');
                                            
                                            let finalOutput = '';
                                            if (accumulatedReasoning.trim()) {
                                                finalOutput += `【深度思考推理过程】\n${accumulatedReasoning.trim()}\n\n【参考解析】\n`;
                                            }
                                            finalOutput += accumulatedSolution;
                                            resultBox.textContent = finalOutput;
                                            
                                            loadToFinalReview('ai');
                                            showToast('AI 解析生成成功！');
                                            finishCurrentRequest();
                                        }, 300);
                                        return;
                                    }
                                } catch (e) {
                                    console.error('Failed to parse SSE line:', line, e);
                                }
                            }
                        }
                        return read();
                    });
                }
                
                return read();
            })
            .catch(err => {
                // A superseded request must not clear or re-enable the controls
                // owned by the new request.
                if (!requestIsCurrent()) return;
                finishCurrentRequest(true);
                if (err.name !== 'AbortError') {
                    showToast('AI 生成解析出错: ' + err.message, 'error');
                }
            });
        }

        // Import Tab results into persistent Final Review Textbox
        function loadToFinalReview(source) {
            const finalEdit = document.getElementById('editAnswerMarkdown');
            let contentToImport = '';
            
            if (source === 'ai') {
                contentToImport = document.getElementById('aiResultText').textContent;
                // Exclude reasoning block from importing into the final review editor
                if (contentToImport.includes('【参考解析】')) {
                    const parts = contentToImport.split('【参考解析】');
                    contentToImport = parts[1] || parts[0];
                }
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
            if (typeof window.extractTikzCodeFromTextarea === 'function') {
                window.extractTikzCodeFromTextarea('editAnswerMarkdown');
            }
        }

        // Clear Draft
