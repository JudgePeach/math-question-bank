/**
 * paper.js - 本地化数学题库组卷系统 (Paper Studio)
 * 纯 Vanilla JS + 渐进式级联架构
 * 严禁使用正则后行断言 (?<!...) 和 (?<=...)
 */

(function () {
    'use strict';

    // Local Storage Keys
    const STORAGE_KEY_CART = 'mathbank_paper_cart';
    const STORAGE_KEY_META = 'mathbank_paper_meta';
    const STORAGE_KEY_COLLAPSED = 'mathbank_paper_filter_collapsed';
    const PAPER_STREAM_PAGE_SIZE = 15;

    // Global Store State
    window.PaperStore = {
        cart: [], // Array of { id: number, score: number }
        meta: {
            title: '2026年高中数学模拟考试试卷',
            subtitle: '',
            paper_type: 'exam_19',
            section_order: [],
            solution_space_default: '7.0',
            show_notice: true,
            show_secret: true
        },
        filters: {
            compulsory: '',
            chapter: '',
            knowledge: '',
            question_type: '',
            difficulty: '',
            keyword: '',
            tab: 'all' // 'all' or 'selected'
        },
        isFilterCollapsed: false,
        bankQuestions: [], // Current server-paginated question bank page
        streamPagination: {
            all: { page: 1, total: null, totalPages: 1, loading: false, error: '', retryPage: 1 },
            selected: { page: 1 }
        },
        cartQuestionLoad: {
            loading: false,
            error: '',
            missingIds: [],
            confirmedMissingIds: [],
            failedIds: []
        },
        questionsMap: {}, // qid -> Question Object
        answerCache: Object.create(null), // qid -> full answer_markdown, loaded on demand
        expandedAnswerIds: new Set(),
        answerLoadingIds: new Set(),
        answerErrors: Object.create(null),
        activeWorkspace: 'dashboard'
    };

    // Load State from LocalStorage
    function loadStateFromStorage() {
        try {
            const rawCart = localStorage.getItem(STORAGE_KEY_CART);
            if (rawCart) {
                window.PaperStore.cart = JSON.parse(rawCart);
            }
        } catch (e) {
            window.PaperStore.cart = [];
        }

        try {
            const rawMeta = localStorage.getItem(STORAGE_KEY_META);
            if (rawMeta) {
                window.PaperStore.meta = Object.assign({}, window.PaperStore.meta, JSON.parse(rawMeta));
            }
        } catch (e) { }

        try {
            window.PaperStore.isFilterCollapsed = localStorage.getItem(STORAGE_KEY_COLLAPSED) === 'true';
        } catch (e) { }
    }

    function saveCartToStorage() {
        try {
            if (window.PaperStore.cart.length === 0) {
                localStorage.removeItem(STORAGE_KEY_CART);
            } else {
                localStorage.setItem(STORAGE_KEY_CART, JSON.stringify(window.PaperStore.cart));
            }
        } catch (e) { }
        const loadState = window.PaperStore.cartQuestionLoad;
        if (loadState) {
            const cartIds = new Set(window.PaperStore.cart
                .map(item => parseInt(item.id, 10))
                .filter(qid => qid > 0));
            const missingIds = getMissingCartQuestionIds();
            loadState.missingIds = missingIds;
            loadState.confirmedMissingIds = (loadState.confirmedMissingIds || [])
                .map(qid => parseInt(qid, 10))
                .filter(qid => cartIds.has(qid));
            loadState.failedIds = (loadState.failedIds || [])
                .map(qid => parseInt(qid, 10))
                .filter(qid => cartIds.has(qid));
            if (missingIds.length === 0
                    && loadState.confirmedMissingIds.length === 0
                    && loadState.failedIds.length === 0) {
                loadState.error = '';
                if (!cartQuestionsLoadPromise) loadState.loading = false;
            } else if (loadState.confirmedMissingIds.length > 0
                    || loadState.failedIds.length > 0) {
                loadState.error = buildCartQuestionLoadError(
                    loadState.confirmedMissingIds,
                    loadState.failedIds
                );
            }
        }
        updateCartBadges();
    }

    function saveMetaToStorage() {
        try {
            localStorage.setItem(STORAGE_KEY_META, JSON.stringify(window.PaperStore.meta));
        } catch (e) { }
    }

    function getQuestionFigAlign(q) {
        const defaultAlign = (window.PaperStore.meta.paper_type === 'quiz') ? 'bottom_right' : 'right';
        if (!q) return defaultAlign;
        if (q.custom_figure_align) return q.custom_figure_align;
        if (q.figure_align_custom && ['right', 'bottom_left', 'center', 'bottom_right'].includes(q.figure_align)) {
            return q.figure_align;
        }
        if (q.figure_align && q.figure_align !== 'right') return q.figure_align;
        return defaultAlign;
    }

    function hasCachedPaperAnswer(qid) {
        return Object.prototype.hasOwnProperty.call(window.PaperStore.answerCache, qid);
    }

    function seedPaperAnswerCache(q) {
        if (!q || !q.id || typeof q.answer_markdown !== 'string') return;
        window.PaperStore.answerCache[q.id] = q.answer_markdown;
        q.has_answer = Boolean(q.answer_markdown.trim());
    }

    async function loadPaperQuestionAnswer(qid) {
        qid = parseInt(qid, 10);
        if (!qid || window.PaperStore.answerLoadingIds.has(qid)) return;

        const q = window.PaperStore.questionsMap[qid];
        if (q) seedPaperAnswerCache(q);
        if (hasCachedPaperAnswer(qid)) {
            renderPart3QuestionStream();
            return;
        }

        window.PaperStore.answerLoadingIds.add(qid);
        delete window.PaperStore.answerErrors[qid];
        renderPart3QuestionStream();

        try {
            const response = await fetch(`/api/questions/${qid}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const detail = await response.json();
            const answer = typeof detail.answer_markdown === 'string'
                ? detail.answer_markdown
                : '';
            window.PaperStore.answerCache[qid] = answer;
            if (q) {
                q.answer_markdown = answer;
                q.has_answer = Boolean(answer.trim());
            }
        } catch (error) {
            console.error('Load paper question answer error:', error);
            window.PaperStore.answerErrors[qid] = '答案加载失败，请重试。';
        } finally {
            window.PaperStore.answerLoadingIds.delete(qid);
            renderPart3QuestionStream();
        }
    }

    window.togglePaperQuestionAnswer = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        if (window.PaperStore.expandedAnswerIds.has(qid)) {
            window.PaperStore.expandedAnswerIds.delete(qid);
            renderPart3QuestionStream();
            return;
        }

        window.PaperStore.expandedAnswerIds.add(qid);
        loadPaperQuestionAnswer(qid);
    };

    window.retryPaperQuestionAnswer = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        delete window.PaperStore.answerErrors[qid];
        delete window.PaperStore.answerCache[qid];
        loadPaperQuestionAnswer(qid);
    };

    window.collapseAllPaperAnswers = function () {
        window.PaperStore.expandedAnswerIds.clear();
        renderPart3QuestionStream();
    };

    // Public Cart Helper Functions
    window.isInCart = function (qid) {
        qid = parseInt(qid, 10);
        return window.PaperStore.cart.some(item => item.id === qid);
    };

    window.addToCart = function (qid, score = null) {
        qid = parseInt(qid, 10);
        if (!window.isInCart(qid)) {
            const q = window.PaperStore.questionsMap[qid];
            if (!score && q) {
                score = q.question_type === 'detailed_answer' ? 12 : 5;
            } else if (!score) {
                score = 5;
            }
            window.PaperStore.cart.push({ id: qid, score: parseInt(score, 10) || 5 });
            saveCartToStorage();
            const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
            if (window.showToast) window.showToast(`已将题目 #${seqNum} 加入试卷`, 'success');
            
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        }
    };

    window.removeFromCart = function (qid) {
        qid = parseInt(qid, 10);
        window.PaperStore.cart = window.PaperStore.cart.filter(item => item.id !== qid);
        saveCartToStorage();
        if (window.PaperStore.activeWorkspace === 'paper') {
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        }
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`已将题目 #${seqNum} 移出试卷`, 'info');
    };

    window.toggleCart = function (qid, score = null) {
        qid = parseInt(qid, 10);
        if (window.isInCart(qid)) {
            window.removeFromCart(qid);
        } else {
            window.addToCart(qid, score);
        }
    };

    window.clearCart = function () {
        if (confirm('确定要清空已加入试卷的所有题目吗？')) {
            window.PaperStore.cart = [];
            saveCartToStorage();
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
            if (window.showToast) window.showToast('已清空试卷题目', 'info');
        }
    };

    function updateCartBadges() {
        const count = window.PaperStore.cart.length;
        const badges = document.querySelectorAll('.paper-cart-badge');
        badges.forEach(b => {
            b.textContent = count;
            if (count > 0) {
                b.classList.remove('hidden');
            } else {
                b.classList.add('hidden');
            }
        });
    }

    function initPaperSplitResizer() {
        const workspaceBody = document.querySelector('.paper-studio-body');
        const library = document.querySelector('.paper-library-column');
        const preview = document.querySelector('.paper-preview-column');
        const resizer = document.getElementById('paperSplitResizer');
        if (!workspaceBody || !library || !preview || !resizer) return;

        const minRatio = Number(resizer.getAttribute('aria-valuemin')) || 36;
        const maxRatio = Number(resizer.getAttribute('aria-valuemax')) || 66;
        const defaultRatio = 58;
        let isDragging = false;

        function setPaperSplitRatio(value, { notify = false } = {}) {
            const ratio = Math.min(maxRatio, Math.max(minRatio, Number(value) || defaultRatio));
            workspaceBody.style.setProperty('--paper-library-track', `${ratio}fr`);
            workspaceBody.style.setProperty('--paper-preview-track', `${100 - ratio}fr`);
            resizer.setAttribute('aria-valuenow', String(Math.round(ratio)));
            if (notify) window.dispatchEvent(new Event('resize'));
            return ratio;
        }

        function ratioFromPointer(clientX) {
            const libraryRect = library.getBoundingClientRect();
            const previewRect = preview.getBoundingClientRect();
            const dividerWidth = resizer.getBoundingClientRect().width;
            const availableWidth = Math.max(1, previewRect.right - libraryRect.left - dividerWidth);
            const desiredLibraryWidth = clientX - libraryRect.left - dividerWidth / 2;
            return desiredLibraryWidth / availableWidth * 100;
        }

        function finishDragging(event) {
            if (!isDragging) return;
            isDragging = false;
            resizer.classList.remove('is-dragging');
            document.body.style.cursor = '';
            document.body.classList.remove('select-none');
            if (event && resizer.hasPointerCapture && resizer.hasPointerCapture(event.pointerId)) {
                resizer.releasePointerCapture(event.pointerId);
            }
            window.dispatchEvent(new Event('resize'));
        }

        resizer.addEventListener('pointerdown', event => {
            if (window.matchMedia('(max-width: 960px)').matches) return;
            event.preventDefault();
            isDragging = true;
            resizer.classList.add('is-dragging');
            document.body.style.cursor = 'col-resize';
            document.body.classList.add('select-none');
            if (resizer.setPointerCapture) resizer.setPointerCapture(event.pointerId);
            setPaperSplitRatio(ratioFromPointer(event.clientX));
        });

        resizer.addEventListener('pointermove', event => {
            if (!isDragging) return;
            event.preventDefault();
            setPaperSplitRatio(ratioFromPointer(event.clientX));
        });

        resizer.addEventListener('pointerup', finishDragging);
        resizer.addEventListener('pointercancel', finishDragging);

        resizer.addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
            event.preventDefault();
            const currentRatio = Number(resizer.getAttribute('aria-valuenow')) || defaultRatio;
            const nextRatio = event.key === 'Home'
                ? defaultRatio
                : currentRatio + (event.key === 'ArrowLeft' ? -2 : 2);
            setPaperSplitRatio(nextRatio, { notify: true });
        });

        resizer.addEventListener('dblclick', () => {
            setPaperSplitRatio(defaultRatio, { notify: true });
        });

        setPaperSplitRatio(defaultRatio);
        window.setPaperSplitRatio = setPaperSplitRatio;
    }

    // Import is authored after the app shell so its large markup stays isolated,
    // then mounted beside the bank and paper sections as a peer workspace.
    const mainWorkspaceContainer = document.querySelector('#appContentShell > main');
    const importWorkspaceSection = document.getElementById('importWorkspaceSection');
    const paperWorkspaceSection = document.getElementById('paperWorkspaceSection');
    if (
        mainWorkspaceContainer
        && importWorkspaceSection
        && paperWorkspaceSection
        && importWorkspaceSection.parentElement !== mainWorkspaceContainer
    ) {
        mainWorkspaceContainer.insertBefore(importWorkspaceSection, paperWorkspaceSection);
    }

    // Workspace View Switcher
    const originalSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        // The initial flash-prevention class keeps the first workspace visible
        // during page boot.  Once the user makes an explicit workspace choice,
        // remove it so its !important rules cannot mask the target workspace.
        document.documentElement.classList.remove('init-ws-dashboard', 'init-ws-paper');
        const previousWorkspace = window.PaperStore.activeWorkspace || 'dashboard';
        if (typeof originalSelectWorkspace === 'function') {
            originalSelectWorkspace(workspaceId, workspaceName);
        }

        const importSec = document.getElementById('importWorkspaceSection');
        const recordsSec = document.getElementById('recordsWorkspaceSection');
        const dashboardSec = document.getElementById('dashboardWorkspaceSection');
        if (workspaceId === 'import' && previousWorkspace !== 'import' && importSec) {
            importSec.dataset.returnNavTarget = previousWorkspace;
        }

        window.PaperStore.activeWorkspace = workspaceId;
        try {
            localStorage.setItem('mathbank_active_workspace', workspaceId);
            if (window.__serverInstanceId) {
                localStorage.setItem('mathbank_server_instance_id', window.__serverInstanceId);
            }
        } catch (e) { }

        const bankSec = document.getElementById('bankWorkspaceSection');
        const paperSec = document.getElementById('paperWorkspaceSection');
        const toggleSidebarBtn = document.getElementById('toggleSidebarBtn');

        if (dashboardSec) dashboardSec.classList.add('hidden');
        if (bankSec) bankSec.classList.add('hidden');
        if (paperSec) paperSec.classList.add('hidden');
        if (importSec) importSec.classList.add('hidden');
        if (recordsSec) recordsSec.classList.add('hidden');
        if (toggleSidebarBtn) toggleSidebarBtn.classList.add('hidden');

        if (workspaceId === 'dashboard') {
            if (dashboardSec) {
                dashboardSec.classList.remove('hidden');
                if (typeof window.loadDashboardData === 'function') {
                    window.loadDashboardData();
                }
            }
        } else if (workspaceId === 'paper') {
            if (paperSec) {
                paperSec.classList.remove('hidden');
                window.renderPaperWorkspace();
            }
        } else if (workspaceId === 'import') {
            if (importSec) importSec.classList.remove('hidden');
        } else if (workspaceId === 'records') {
            if (recordsSec) recordsSec.classList.remove('hidden');
        } else {
            if (toggleSidebarBtn) toggleSidebarBtn.classList.remove('hidden');
            if (bankSec) bankSec.classList.remove('hidden');
        }
    };

    // Toggle Part 2 Filter Bar Collapsing
    window.togglePaperFilterBar = function () {
        window.PaperStore.isFilterCollapsed = !window.PaperStore.isFilterCollapsed;
        try {
            localStorage.setItem(STORAGE_KEY_COLLAPSED, window.PaperStore.isFilterCollapsed ? 'true' : 'false');
        } catch (e) { }

        const filterBox = document.getElementById('paperFilterSection');
        const toggleIcon = document.getElementById('paperFilterToggleIcon');
        const toggleTxt = document.getElementById('paperFilterToggleTxt');

        if (!filterBox) return;

        if (window.PaperStore.isFilterCollapsed) {
            filterBox.style.maxHeight = '0px';
            filterBox.style.opacity = '0';
            filterBox.style.overflow = 'hidden';
            filterBox.style.paddingTop = '0px';
            filterBox.style.paddingBottom = '0px';
            filterBox.style.marginTop = '0px';
            filterBox.style.marginBottom = '0px';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-down text-xs';
            if (toggleTxt) toggleTxt.textContent = '展开组卷配置栏';
        } else {
            filterBox.style.maxHeight = '700px';
            filterBox.style.opacity = '1';
            filterBox.style.overflow = '';
            filterBox.style.paddingTop = '';
            filterBox.style.paddingBottom = '';
            filterBox.style.marginTop = '';
            filterBox.style.marginBottom = '';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-up text-xs';
            if (toggleTxt) toggleTxt.textContent = '收起组卷配置栏';
        }
    };

    // Move Question Order
    window.movePaperQuestion = function (index, direction) {
        const cart = window.PaperStore.cart;
        let newIndex = index;
        const previousSelectedPage = window.PaperStore.streamPagination.selected.page;
        if (direction === 'up' && index > 0) {
            const temp = cart[index];
            cart[index] = cart[index - 1];
            cart[index - 1] = temp;
            newIndex = index - 1;
            if (window.PaperStore.filters.tab === 'selected') {
                window.PaperStore.streamPagination.selected.page = Math.floor(newIndex / PAPER_STREAM_PAGE_SIZE) + 1;
            }
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
            if (window.PaperStore.streamPagination.selected.page !== previousSelectedPage) {
                scrollPaperQuestionStreamToTop();
            }
        } else if (direction === 'down' && index < cart.length - 1) {
            const temp = cart[index];
            cart[index] = cart[index + 1];
            cart[index + 1] = temp;
            newIndex = index + 1;
            if (window.PaperStore.filters.tab === 'selected') {
                window.PaperStore.streamPagination.selected.page = Math.floor(newIndex / PAPER_STREAM_PAGE_SIZE) + 1;
            }
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
            if (window.PaperStore.streamPagination.selected.page !== previousSelectedPage) {
                scrollPaperQuestionStreamToTop();
            }
        }
    };

    // Update Score
    window.updatePaperQuestionScore = function (qid, newScore) {
        qid = parseInt(qid, 10);
        newScore = parseInt(newScore, 10) || 5;
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (item) {
            item.score = newScore;
            saveCartToStorage();
            window.renderPaperCanvas();
        }
    };

    let bankQuestionsAbortController = null;
    let bankQuestionsRequestSeq = 0;
    let cartQuestionsLoadPromise = null;
    const paperActionInFlight = new Set();

    function getPaperCartSignature() {
        return JSON.stringify({
            paper_type: (window.PaperStore.meta || {}).paper_type,
            section_order: normalizeSectionOrder((window.PaperStore.meta || {}).section_order),
            questions: window.PaperStore.cart.map(item => [
            parseInt(item.id, 10) || 0,
            parseInt(item.score, 10) || 0,
            item.solution_space === undefined ? null : String(item.solution_space)
        ])});
    }

    function beginPaperAction(actionKey, actionLabel) {
        if (paperActionInFlight.has(actionKey)) {
            if (window.showToast) window.showToast(`${actionLabel}正在进行，请稍候。`, 'info');
            return false;
        }
        paperActionInFlight.add(actionKey);
        return true;
    }

    function finishPaperAction(actionKey) {
        paperActionInFlight.delete(actionKey);
    }

    function isPaperCartSnapshotCurrent(expectedSignature, actionLabel) {
        if (getPaperCartSignature() === expectedSignature) return true;
        if (window.showToast) {
            window.showToast(`卷面题目已变化，本次${actionLabel}已停止，请重新操作。`, 'warning');
        }
        return false;
    }

    function clampPaperStreamPage(page, totalPages) {
        const safeTotalPages = Math.max(1, parseInt(totalPages, 10) || 1);
        return Math.max(1, Math.min(parseInt(page, 10) || 1, safeTotalPages));
    }

    function getSelectedPaperTotalPages() {
        return Math.max(1, Math.ceil(window.PaperStore.cart.length / PAPER_STREAM_PAGE_SIZE));
    }

    function clampStoredPaperStreamPages() {
        const pagination = window.PaperStore.streamPagination;
        pagination.all.page = clampPaperStreamPage(pagination.all.page, pagination.all.totalPages);
        pagination.selected.page = clampPaperStreamPage(
            pagination.selected.page,
            getSelectedPaperTotalPages()
        );
    }

    function cancelBankQuestionsFetch() {
        bankQuestionsRequestSeq += 1;
        if (bankQuestionsAbortController) {
            bankQuestionsAbortController.abort();
            bankQuestionsAbortController = null;
        }
        const pagination = window.PaperStore.streamPagination.all;
        pagination.loading = false;
    }

    function getMissingCartQuestionIds() {
        return Array.from(new Set(
            window.PaperStore.cart
                .map(item => parseInt(item.id, 10))
                .filter(qid => qid && !window.PaperStore.questionsMap[qid])
        ));
    }

    function uniquePaperCartQuestionIds() {
        return Array.from(new Set(
            window.PaperStore.cart
                .map(item => parseInt(item.id, 10))
                .filter(qid => qid > 0)
        ));
    }

    function buildCartQuestionLoadError(confirmedMissingIds, failedIds) {
        const messages = [];
        if (confirmedMissingIds.length > 0) {
            messages.push(`有 ${confirmedMissingIds.length} 道已选题目已删除或不存在`);
        }
        if (failedIds.length > 0) {
            messages.push(`有 ${failedIds.length} 道已选题目暂未通过服务端核验`);
        }
        return messages.length > 0 ? `${messages.join('；')}。` : '';
    }

    async function ensureCartQuestionsLoaded(options = {}) {
        const revalidateAll = Boolean(options && options.revalidateAll);
        if (cartQuestionsLoadPromise) await cartQuestionsLoadPromise;

        const loadState = window.PaperStore.cartQuestionLoad;
        const cartIds = uniquePaperCartQuestionIds();
        const cartIdSet = new Set(cartIds);
        const missingIds = getMissingCartQuestionIds();
        const idsToLoad = revalidateAll ? cartIds : missingIds;
        const previousConfirmedMissingIds = (loadState.confirmedMissingIds || [])
            .map(qid => parseInt(qid, 10))
            .filter(qid => cartIdSet.has(qid));
        const previousFailedIds = (loadState.failedIds || [])
            .map(qid => parseInt(qid, 10))
            .filter(qid => cartIdSet.has(qid));

        if (cartIds.length === 0) {
            loadState.loading = false;
            loadState.error = '';
            loadState.missingIds = [];
            loadState.confirmedMissingIds = [];
            loadState.failedIds = [];
            clampStoredPaperStreamPages();
            return true;
        }

        if (idsToLoad.length === 0) {
            loadState.loading = false;
            loadState.missingIds = [];
            loadState.confirmedMissingIds = previousConfirmedMissingIds;
            loadState.failedIds = previousFailedIds;
            loadState.error = buildCartQuestionLoadError(
                previousConfirmedMissingIds,
                previousFailedIds
            );
            clampStoredPaperStreamPages();
            return previousConfirmedMissingIds.length === 0 && previousFailedIds.length === 0;
        }

        loadState.loading = true;
        loadState.error = '';
        loadState.missingIds = missingIds;
        const confirmedMissingIds = new Set(revalidateAll ? [] : previousConfirmedMissingIds);
        const failedIds = new Set(revalidateAll ? [] : previousFailedIds);
        const previouslyConfirmedIds = new Set(previousConfirmedMissingIds);
        idsToLoad.forEach(qid => {
            confirmedMissingIds.delete(qid);
            failedIds.delete(qid);
        });

        const activeLoadPromise = (async () => {
            for (let start = 0; start < idsToLoad.length; start += 50) {
                const batchIds = idsToLoad.slice(start, start + 50);
                const protectedFigureLayouts = snapshotFigureLayoutsForBankFetch();
                try {
                    const params = new URLSearchParams({ ids: batchIds.join(',') });
                    const response = await fetch(`/api/paper/questions?${params.toString()}`);
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const payload = await response.json();
                    if (!payload || payload.status !== 'success' || !Array.isArray(payload.data)) {
                        throw new Error('Invalid paper question response');
                    }
                    const returnedIds = new Set();
                    payload.data.forEach(question => {
                        const qid = parseInt(question && question.id, 10);
                        if (!qid || !batchIds.includes(qid)) return;
                        returnedIds.add(qid);
                        preserveNewerFigureLayout(question, protectedFigureLayouts);
                        window.PaperStore.questionsMap[qid] = question;
                        seedPaperAnswerCache(question);
                    });
                    batchIds.forEach(qid => {
                        failedIds.delete(qid);
                        if (returnedIds.has(qid)) {
                            confirmedMissingIds.delete(qid);
                        } else {
                            confirmedMissingIds.add(qid);
                        }
                    });
                } catch (error) {
                    console.error('Load cart question batch error:', error);
                    batchIds.forEach(qid => {
                        if (previouslyConfirmedIds.has(qid)) {
                            confirmedMissingIds.add(qid);
                            failedIds.delete(qid);
                        } else {
                            confirmedMissingIds.delete(qid);
                            failedIds.add(qid);
                        }
                    });
                }
            }
        })();
        cartQuestionsLoadPromise = activeLoadPromise;

        try {
            await activeLoadPromise;
        } finally {
            if (cartQuestionsLoadPromise === activeLoadPromise) {
                cartQuestionsLoadPromise = null;
            }
        }
        const currentCartIds = new Set(uniquePaperCartQuestionIds());
        const confirmedCurrentIds = Array.from(confirmedMissingIds)
            .filter(qid => currentCartIds.has(qid));
        const failedCurrentIds = Array.from(failedIds)
            .filter(qid => currentCartIds.has(qid) && !confirmedMissingIds.has(qid));
        confirmedCurrentIds.forEach(qid => {
            delete window.PaperStore.questionsMap[qid];
            window.PaperStore.expandedAnswerIds.delete(qid);
            delete window.PaperStore.answerErrors[qid];
            delete window.PaperStore.answerCache[qid];
        });
        const unresolvedIds = getMissingCartQuestionIds();
        loadState.loading = false;
        loadState.missingIds = unresolvedIds;
        loadState.confirmedMissingIds = confirmedCurrentIds;
        loadState.failedIds = failedCurrentIds;
        loadState.error = buildCartQuestionLoadError(confirmedCurrentIds, failedCurrentIds);
        clampStoredPaperStreamPages();
        return unresolvedIds.length === 0
            && confirmedCurrentIds.length === 0
            && failedCurrentIds.length === 0;
    }

    async function ensurePaperCartReady(actionLabel, expectedSignature = null) {
        const complete = await ensureCartQuestionsLoaded({ revalidateAll: true });
        renderPart3QuestionStream();
        window.renderPaperCanvas();
        if (expectedSignature !== null && !isPaperCartSnapshotCurrent(expectedSignature, actionLabel)) {
            return false;
        }
        if (!complete) {
            const message = window.PaperStore.cartQuestionLoad.error || '已选题目尚未完整加载。';
            if (window.showToast) {
                window.showToast(`${message} 暂不能${actionLabel}。`, 'error');
            }
        }
        return complete;
    }

    // Fetch one server-paginated page for the Question Bank Stream.
    async function fetchBankQuestions(requestedPage = null) {
        const protectedFigureLayouts = snapshotFigureLayoutsForBankFetch();
        const f = window.PaperStore.filters;
        const pagination = window.PaperStore.streamPagination.all;
        const targetPage = Math.max(1, parseInt(requestedPage, 10) || pagination.page || 1);
        const params = new URLSearchParams();
        if (f.compulsory) {
            params.append('compulsory', f.compulsory);
            params.append('category_compulsory', f.compulsory);
        }
        if (f.chapter) {
            params.append('chapter', f.chapter);
            params.append('category_chapter', f.chapter);
        }
        if (f.knowledge) {
            params.append('knowledge', f.knowledge);
            params.append('category_knowledge', f.knowledge);
        }
        if (f.question_type) {
            params.append('qtype', f.question_type);
            params.append('question_type', f.question_type);
        }
        if (f.difficulty) {
            params.append('difficulty', f.difficulty);
        }
        if (f.keyword) {
            params.append('q', f.keyword);
            params.append('search', f.keyword);
        }
        params.set('page', String(targetPage));
        params.set('page_size', String(PAPER_STREAM_PAGE_SIZE));
        params.set('sort', 'desc');

        const requestSeq = ++bankQuestionsRequestSeq;
        if (bankQuestionsAbortController) bankQuestionsAbortController.abort();
        const controller = new AbortController();
        bankQuestionsAbortController = controller;
        pagination.loading = true;
        pagination.error = '';
        pagination.retryPage = targetPage;
        if ((window.PaperStore.filters.tab || 'all') === 'all') {
            renderPart3QuestionStream();
        }

        try {
            const res = await fetch(`/api/questions?${params.toString()}`, {
                signal: controller.signal
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const payload = await res.json();
            if (requestSeq !== bankQuestionsRequestSeq) return false;
            if (payload && Array.isArray(payload.items)) {
                payload.items.forEach(q => preserveNewerFigureLayout(q, protectedFigureLayouts));
                window.PaperStore.bankQuestions = payload.items;
                pagination.total = Math.max(0, parseInt(payload.total, 10) || 0);
                pagination.totalPages = Math.max(1, parseInt(payload.total_pages, 10) || 1);
                pagination.page = clampPaperStreamPage(payload.page, pagination.totalPages);
                pagination.loading = false;
                pagination.error = '';
                payload.items.forEach(q => {
                    window.PaperStore.questionsMap[q.id] = q;
                });
                return true;
            }
            throw new Error('Invalid paginated question response');
        } catch (e) {
            if (e && e.name === 'AbortError') return false;
            console.error('Fetch bank questions error:', e);
            if (requestSeq === bankQuestionsRequestSeq) {
                pagination.loading = false;
                pagination.error = '题库加载失败，请检查服务状态后重试。';
                return true;
            }
        } finally {
            if (requestSeq === bankQuestionsRequestSeq) {
                bankQuestionsAbortController = null;
            }
        }
        return false;
    }

    // Render Full Paper Workspace (Part 2, Part 3, Part 4)
    window.renderPaperWorkspace = async function () {
        await fetchBankQuestions();
        await ensureCartQuestionsLoaded({ revalidateAll: true });
        renderPart2FilterSection();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    // Render Part 2: Config & 3-Level Cascade Filter Section
    function renderPart2FilterSection() {
        const meta = window.PaperStore.meta;
        const f = window.PaperStore.filters;
        const container = document.getElementById('paperFilterSection');
        if (!container) return;

        const tree = window.categoryTree || {};
        const metadata = window.systemMetadata || {};

        // 1. Build Compulsory Book options
        let bookOptions = `<option value="">-- 选择学段 --</option>`;
        Object.keys(tree).forEach(b => {
            bookOptions += `<option value="${escapeHtml(b)}" ${f.compulsory === b ? 'selected' : ''}>${escapeHtml(b)}</option>`;
        });

        // 2. Build Chapter options (Level 2)
        let chapterOptions = `<option value="">-- 先选学段 --</option>`;
        let isChapterDisabled = true;
        if (f.compulsory && tree[f.compulsory]) {
            isChapterDisabled = false;
            chapterOptions = `<option value="">-- 所有章节 --</option>`;
            Object.keys(tree[f.compulsory]).forEach(ch => {
                chapterOptions += `<option value="${escapeHtml(ch)}" ${f.chapter === ch ? 'selected' : ''}>${escapeHtml(ch)}</option>`;
            });
        }

        // 3. Build Knowledge options (Level 3)
        let knowledgeOptions = `<option value="">-- 先选章节 --</option>`;
        let isKnowledgeDisabled = true;
        if (f.compulsory && f.chapter && tree[f.compulsory] && tree[f.compulsory][f.chapter]) {
            isKnowledgeDisabled = false;
            knowledgeOptions = `<option value="">-- 所有小节/知识点 --</option>`;
            const knowList = tree[f.compulsory][f.chapter];
            if (Array.isArray(knowList)) {
                knowList.forEach(k => {
                    knowledgeOptions += `<option value="${escapeHtml(k)}" ${f.knowledge === k ? 'selected' : ''}>${escapeHtml(k)}</option>`;
                });
            }
        }

        // 4. Build Question Type options
        let qTypeOptions = `<option value="">全部题型</option>`;
        const qTypes = metadata.question_types || [
            { value: 'single_choice', label: '单选题' },
            { value: 'multi_choice', label: '多选题' },
            { value: 'fill_in_blank', label: '填空题' },
            { value: 'detailed_answer', label: '解答题' }
        ];
        qTypes.forEach(t => {
            qTypeOptions += `<option value="${escapeHtml(t.value)}" ${f.question_type === t.value ? 'selected' : ''}>${escapeHtml(t.label)}</option>`;
        });

        // 5. Build Difficulty options
        let diffOptions = `<option value="">全部难度</option>`;
        const difficulties = metadata.difficulties || [
            { value: 'easy', label: '普通题' },
            { value: 'easy_error', label: '易错题' },
            { value: 'medium', label: '挑战题' },
            { value: 'hard', label: '强基题' }
        ];
        difficulties.forEach(d => {
            diffOptions += `<option value="${escapeHtml(d.value)}" ${f.difficulty === d.value ? 'selected' : ''}>${escapeHtml(d.label)}</option>`;
        });

        container.innerHTML = `
            <div class="space-y-2 bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl shadow-sm">
                <!-- Top Row: Paper Metadata -->
                <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">主标题</label>
                        <input type="text" id="paperMetaTitle" value="${escapeHtml(meta.title)}" 
                            oninput="updatePaperMeta('title', this.value)" onchange="updatePaperMeta('title', this.value)"
                            class="glass-input w-full px-2 py-1 text-xs rounded-lg" placeholder="如：2026年高中数学期末考试">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">副标题 / 备注</label>
                        <input type="text" id="paperMetaSubtitle" value="${escapeHtml(meta.subtitle)}" 
                            oninput="updatePaperMeta('subtitle', this.value)" onchange="updatePaperMeta('subtitle', this.value)"
                            class="glass-input w-full px-2 py-1 text-xs rounded-lg" placeholder="可选副标题/说明...">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">试卷类型预设</label>
                        <select id="paperMetaType" onchange="updatePaperMeta('paper_type', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            <option value="exam_19" ${meta.paper_type === 'exam_19' ? 'selected' : ''}>19题高考卷 (含答题卡)</option>
                            <option value="exam" ${meta.paper_type === 'exam' ? 'selected' : ''}>常规试卷</option>
                            <option value="quiz" ${meta.paper_type === 'quiz' ? 'selected' : ''}>日常小练</option>
                        </select>
                    </div>
                </div>

                <!-- Middle Row 1: 3-Level Cascade Curriculum Dropdowns (学段 -> 章节 -> 小节/知识点) -->
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1.5 border-t border-slate-100 dark:border-slate-800/60">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">学段</label>
                        <select id="paperFilterCompulsory" onchange="onPaperFilterChange('compulsory', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${bookOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">章节</label>
                        <select id="paperFilterChapter" onchange="onPaperFilterChange('chapter', this.value)" ${isChapterDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${chapterOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">小节 / 知识点</label>
                        <select id="paperFilterKnowledge" onchange="onPaperFilterChange('knowledge', this.value)" ${isKnowledgeDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${knowledgeOptions}
                        </select>
                    </div>
                </div>

                <!-- Middle Row 2: Question Type, Difficulty & Search Input -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">题型</label>
                        <select id="paperFilterType" onchange="onPaperFilterChange('question_type', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${qTypeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">难度</label>
                        <select id="paperFilterDifficulty" onchange="onPaperFilterChange('difficulty', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${diffOptions}
                        </select>
                    </div>
                    <div class="col-span-2">
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">来源 / 自定义标签 / 关键词</label>
                        <div class="relative">
                            <i class="fa-solid fa-magnifying-glass text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 text-xs"></i>
                            <input type="text" id="paperFilterKeyword" value="${escapeHtml(f.keyword)}"
                                oninput="onPaperFilterChange('keyword', this.value)"
                                class="glass-input w-full pl-7 pr-2.5 py-1 text-xs rounded-lg" placeholder="搜索题干内容 / 来源 / 标签 / 批注...">
                        </div>
                    </div>
                </div>

                <!-- Bottom Row: AI Prompt Selection Bar -->
                <div class="pt-1.5 border-t border-slate-100 dark:border-slate-800/60 flex items-center space-x-2">
                    <div class="relative flex-1">
                        <i class="fa-solid fa-wand-magic-sparkles text-brand-500 absolute left-2.5 top-1/2 -translate-y-1/2 text-[10px]"></i>
                        <input type="text" id="paperAiPromptInput" 
                            class="glass-input w-full pl-7 pr-2.5 py-1 text-[10px] rounded-lg border-brand-200/80"
                            placeholder="智能一键抽卷：例如“帮我抽 5 道难度中等的函数选择题”"
                            onkeypress="if(event.key==='Enter') triggerAiPaperSelect()">
                    </div>
                    <button onclick="triggerAiPaperSelect()" class="glass-btn-primary h-[28px] px-3 rounded-lg text-[10px] font-semibold flex items-center space-x-1 shrink-0">
                        <span>智能抽取</span>
                    </button>
                </div>
            </div>
        `;
    }

    let filterDebounceTimer = null;
    window.onPaperFilterChange = function (key, value) {
        window.PaperStore.filters[key] = value;
        const allPagination = window.PaperStore.streamPagination.all;
        allPagination.page = 1;
        allPagination.total = null;
        allPagination.totalPages = 1;
        allPagination.error = '';
        allPagination.retryPage = 1;
        window.PaperStore.bankQuestions = [];
        cancelBankQuestionsFetch();
        clearTimeout(filterDebounceTimer);
        
        // Handle cascade resets
        if (key === 'compulsory') {
            window.PaperStore.filters.chapter = '';
            window.PaperStore.filters.knowledge = '';
            renderPart2FilterSection();
        } else if (key === 'chapter') {
            window.PaperStore.filters.knowledge = '';
            renderPart2FilterSection();
        }

        if (key === 'keyword') {
            allPagination.loading = true;
            renderPart3QuestionStream();
            filterDebounceTimer = setTimeout(async () => {
                const shouldRender = await fetchBankQuestions(1);
                if (shouldRender) renderPart3QuestionStream();
            }, 300);
        } else {
            fetchBankQuestions(1).then((shouldRender) => {
                if (shouldRender) renderPart3QuestionStream();
            });
        }
    };

    function syncCanvasHeaderMeta(key, value) {
        const cleanVal = (value || '').trim();
        if (key === 'title') {
            const nodes = document.querySelectorAll('.canvas-meta-title');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
            const leftInput = document.getElementById('paperMetaTitle');
            if (leftInput && leftInput !== document.activeElement && leftInput.value !== value) {
                leftInput.value = value;
            }
        } else if (key === 'subtitle') {
            const nodes = document.querySelectorAll('.canvas-meta-subtitle');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
            const leftInput = document.getElementById('paperMetaSubtitle');
            if (leftInput && leftInput !== document.activeElement && leftInput.value !== value) {
                leftInput.value = value;
            }
        }
    }

    window.updatePaperMeta = function (key, value) {
        window.PaperStore.meta[key] = value;
        if (key === 'paper_type') {
            const newDefault = value === 'exam_19' ? '0.0' : '7.0';
            window.PaperStore.meta.solution_space_default = newDefault;
            window.PaperStore.cart.forEach(item => {
                item.solution_space = newDefault;
            });
            saveCartToStorage();
            renderPart2FilterSection();
        }
        saveMetaToStorage();

        if (key === 'title' || key === 'subtitle') {
            syncCanvasHeaderMeta(key, value);
            if (typeof window.scheduleActiveA4Repagination === 'function') {
                window.scheduleActiveA4Repagination();
            }
        } else {
            window.renderPaperCanvas();
        }
    };

    window.triggerAiPaperSelect = async function () {
        const input = document.getElementById('paperAiPromptInput');
        const btn = document.querySelector('button[onclick="triggerAiPaperSelect()"]');
        if (!input || !input.value.trim()) {
            if (window.showToast) window.showToast('请先输入智能抽卷要求', 'warning');
            return;
        }
        const promptText = input.value.trim();
        const origBtnHtml = btn ? btn.innerHTML : '';

        try {
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-brain fa-spin text-xs"></i><span>AI 思考组卷中...</span>`;
            }
            if (window.showToast) window.showToast('正在调用 AI 大模型分析需求并遴选最佳题目...', 'info');
            const f = window.PaperStore.filters;
            const res = await fetch('/api/paper/ai-select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: promptText,
                    limit: 5,
                    compulsory: f.compulsory,
                    chapter: f.chapter,
                    knowledge: f.knowledge,
                    question_type: f.question_type,
                    difficulty: f.difficulty
                })
            });
            const data = await res.json();
            if (data.status === 'success' && Array.isArray(data.data)) {
                let addedCount = 0;
                data.data.forEach(q => {
                    if (!window.isInCart(q.id)) {
                        window.PaperStore.cart.push({ id: q.id, score: q.question_type === 'detailed_answer' ? 12 : 5 });
                        window.PaperStore.questionsMap[q.id] = q;
                        addedCount++;
                    }
                });
                if (data.ai_analysis) {
                    window.PaperStore.meta.ai_analysis = data.ai_analysis;
                    window.PaperStore.meta.ai_model_used = data.model_used || '大模型';
                    saveMetaToStorage();
                }
                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) {
                    const engineLabel = data.fallback ? '算法' : (data.model_used || 'AI 大模型');
                    window.showToast(`AI (${engineLabel}) 成功挑选并加入了 ${addedCount} 道试题`, 'success');
                }
            } else {
                if (window.showToast) window.showToast(data.message || '抽选试题无结果', 'warning');
            }
        } catch (e) {
            if (window.showToast) window.showToast('AI 抽题请求异常', 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origBtnHtml;
            }
        }
    };

    window.clearAiAnalysis = function () {
        delete window.PaperStore.meta.ai_analysis;
        delete window.PaperStore.meta.ai_model_used;
        saveMetaToStorage();
        window.renderPaperCanvas();
    };

    function getPaperStreamPageNumbers(currentPage, totalPages) {
        const safeTotalPages = Math.max(1, parseInt(totalPages, 10) || 1);
        const safeCurrentPage = clampPaperStreamPage(currentPage, safeTotalPages);
        if (safeTotalPages <= 7) {
            return Array.from({ length: safeTotalPages }, (_, index) => index + 1);
        }

        const pages = [1];
        const windowStart = Math.max(2, safeCurrentPage - 1);
        const windowEnd = Math.min(safeTotalPages - 1, safeCurrentPage + 1);
        if (windowStart > 2) pages.push(null);
        for (let page = windowStart; page <= windowEnd; page += 1) pages.push(page);
        if (windowEnd < safeTotalPages - 1) pages.push(null);
        pages.push(safeTotalPages);
        return pages;
    }

    function renderPaperStreamPagination(tabName, currentPage, total, totalPages) {
        const safeTotal = Math.max(0, parseInt(total, 10) || 0);
        const safeTotalPages = Math.max(1, parseInt(totalPages, 10) || 1);
        const safeCurrentPage = clampPaperStreamPage(currentPage, safeTotalPages);
        const pageNumbers = getPaperStreamPageNumbers(safeCurrentPage, safeTotalPages);
        const baseButtonClass = 'min-w-[32px] h-8 px-2 rounded-lg border text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-35';

        return `
            <nav class="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200/70 pt-4 dark:border-slate-700/70"
                aria-label="${tabName === 'selected' ? '已选试题' : '全库试题'}分页">
                <span class="text-xs text-slate-500 dark:text-slate-400">共 ${safeTotal} 题 / ${safeTotalPages} 页</span>
                <div class="flex flex-wrap items-center justify-end gap-1.5">
                    <button type="button" onclick="window.changePaperStreamPage('${tabName}', ${safeCurrentPage - 1})"
                        ${safeCurrentPage <= 1 ? 'disabled' : ''}
                        class="${baseButtonClass} border-slate-200 bg-white text-slate-600 hover:border-brand-200 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        上一页
                    </button>
                    ${pageNumbers.map(page => page === null
                        ? '<span class="min-w-[24px] text-center text-xs text-slate-400" aria-hidden="true">…</span>'
                        : `<button type="button" onclick="window.changePaperStreamPage('${tabName}', ${page})"
                            ${page === safeCurrentPage ? 'aria-current="page"' : ''}
                            class="${baseButtonClass} ${page === safeCurrentPage
                                ? 'border-brand-500 bg-brand-600 text-white shadow-sm'
                                : 'border-slate-200 bg-white text-slate-600 hover:border-brand-200 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'}">
                            ${page}
                        </button>`).join('')}
                    <button type="button" onclick="window.changePaperStreamPage('${tabName}', ${safeCurrentPage + 1})"
                        ${safeCurrentPage >= safeTotalPages ? 'disabled' : ''}
                        class="${baseButtonClass} border-slate-200 bg-white text-slate-600 hover:border-brand-200 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        下一页
                    </button>
                </div>
            </nav>
        `;
    }

    function scrollPaperQuestionStreamToTop() {
        const stream = document.getElementById('paperQuestionStream');
        // Keep pagination inside the list: scrollIntoView also scrolls the page's ancestors.
        if (stream) stream.scrollTop = 0;
    }

    window.retryPaperBankQuestions = async function () {
        const pagination = window.PaperStore.streamPagination.all;
        const shouldRender = await fetchBankQuestions(pagination.retryPage || pagination.page || 1);
        if (shouldRender) renderPart3QuestionStream();
    };

    window.retryPaperCartQuestions = async function () {
        const loadingPromise = ensureCartQuestionsLoaded({ revalidateAll: true });
        renderPart3QuestionStream();
        window.renderPaperCanvas();
        await loadingPromise;
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    window.removeMissingPaperCartQuestions = function () {
        const loadState = window.PaperStore.cartQuestionLoad;
        const removableIds = new Set(
            (loadState.confirmedMissingIds || [])
                .map(qid => parseInt(qid, 10))
                .filter(qid => qid && window.PaperStore.cart.some(
                    item => parseInt(item.id, 10) === qid
                ))
        );
        if (removableIds.size === 0) {
            renderPart3QuestionStream();
            window.renderPaperCanvas();
            return;
        }
        if (!confirm(`确定从卷面移除这 ${removableIds.size} 道已失效题目吗？其他已选题目会保留。`)) return;

        const previousLength = window.PaperStore.cart.length;
        window.PaperStore.cart = window.PaperStore.cart.filter(item => {
            return !removableIds.has(parseInt(item.id, 10));
        });
        const removedCount = previousLength - window.PaperStore.cart.length;
        removableIds.forEach(qid => {
            window.PaperStore.expandedAnswerIds.delete(qid);
            delete window.PaperStore.answerErrors[qid];
            delete window.PaperStore.answerCache[qid];
        });
        loadState.loading = false;
        loadState.confirmedMissingIds = (loadState.confirmedMissingIds || [])
            .filter(qid => !removableIds.has(parseInt(qid, 10)));
        loadState.failedIds = (loadState.failedIds || [])
            .filter(qid => !removableIds.has(parseInt(qid, 10)));
        loadState.missingIds = getMissingCartQuestionIds();
        loadState.error = buildCartQuestionLoadError(
            loadState.confirmedMissingIds,
            loadState.failedIds
        );
        saveCartToStorage();
        clampStoredPaperStreamPages();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
        if (removedCount > 0 && window.showToast) {
            window.showToast(`已移除 ${removedCount} 道失效题目，其他已选题目已保留。`, 'success');
        }
    };

    window.changePaperStreamPage = async function (tabName, requestedPage) {
        if (!['all', 'selected'].includes(tabName)) return;

        if (tabName === 'selected') {
            window.PaperStore.streamPagination.selected.page = clampPaperStreamPage(
                requestedPage,
                getSelectedPaperTotalPages()
            );
            await ensureCartQuestionsLoaded();
            renderPart3QuestionStream();
            scrollPaperQuestionStreamToTop();
            return;
        }

        const allPagination = window.PaperStore.streamPagination.all;
        const targetPage = clampPaperStreamPage(requestedPage, allPagination.totalPages);
        if (targetPage === allPagination.page) return;
        const shouldRender = await fetchBankQuestions(targetPage);
        if (shouldRender) {
            renderPart3QuestionStream();
            scrollPaperQuestionStreamToTop();
        }
    };

    // Switch Part 3 Tab ('all' or 'selected') while preserving each tab's page.
    window.switchPaperStreamTab = async function (tabName) {
        if (!['all', 'selected'].includes(tabName)) return;
        window.PaperStore.filters.tab = tabName;
        clampStoredPaperStreamPages();
        if (tabName === 'selected') {
            const loadingPromise = ensureCartQuestionsLoaded();
            renderPart3QuestionStream();
            await loadingPromise;
        }
        renderPart3QuestionStream();
        scrollPaperQuestionStreamToTop();
    };

    // Render Part 3: Full-Width Question Stream
    function renderPart3QuestionStream() {
        const container = document.getElementById('paperQuestionStream');
        if (!container) return;

        const cart = window.PaperStore.cart;
        const bankQuestions = window.PaperStore.bankQuestions;
        const currentTab = window.PaperStore.filters.tab || 'all';
        const pagination = window.PaperStore.streamPagination;
        const cartLoadState = window.PaperStore.cartQuestionLoad;
        clampStoredPaperStreamPages();
        container.setAttribute(
            'aria-busy',
            (currentTab === 'all' ? pagination.all.loading : cartLoadState.loading) ? 'true' : 'false'
        );

        // Prepare one page while retaining the full-cart index for reorder actions.
        let displayEntries = [];
        let currentPage = pagination.all.page;
        let total = pagination.all.total;
        let totalPages = pagination.all.totalPages;
        if (currentTab === 'selected') {
            total = cart.length;
            totalPages = getSelectedPaperTotalPages();
            currentPage = clampPaperStreamPage(pagination.selected.page, totalPages);
            pagination.selected.page = currentPage;
            const startIndex = (currentPage - 1) * PAPER_STREAM_PAGE_SIZE;
            displayEntries = cart
                .slice(startIndex, startIndex + PAPER_STREAM_PAGE_SIZE)
                .map((item, pageIndex) => ({
                    question: window.PaperStore.questionsMap[item.id],
                    cartIndex: startIndex + pageIndex
                }))
                .filter(entry => Boolean(entry.question));
        } else {
            currentPage = clampPaperStreamPage(pagination.all.page, pagination.all.totalPages);
            pagination.all.page = currentPage;
            displayEntries = bankQuestions.map(question => ({ question, cartIndex: -1 }));
        }
        const displayList = displayEntries.map(entry => entry.question);
        const hasVisibleExpandedAnswers = displayList.some(
            q => q && window.PaperStore.expandedAnswerIds.has(q.id)
        );

        let html = `
            <!-- Part 3 Stream Header Bar -->
            <div id="paperQuestionStreamTop" class="flex flex-wrap items-center justify-between gap-2 pb-3 mb-4 border-b border-slate-200/60 dark:border-slate-700/60">
                <div class="flex items-center space-x-1.5 bg-slate-200/60 p-1 rounded-xl dark:bg-slate-800">
                    <button onclick="switchPaperStreamTab('all')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'all' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        全库试题 (${Number.isInteger(pagination.all.total) ? pagination.all.total : '—'})
                    </button>
                    <button onclick="switchPaperStreamTab('selected')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'selected' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        已选试题 (${cart.length})
                    </button>
                </div>

                <div class="flex flex-wrap items-center justify-end gap-3">
                    ${hasVisibleExpandedAnswers ? `
                        <button type="button" onclick="window.collapseAllPaperAnswers()"
                            class="text-xs font-medium text-slate-500 hover:text-brand-600 transition-colors flex items-center space-x-1">
                            <i class="fa-solid fa-eye-slash text-[10px]"></i>
                            <span>收起全部答案</span>
                        </button>
                    ` : ''}
                    ${cart.length > 0 ? `
                        <button type="button" onclick="window.clearCart()" class="text-xs font-medium text-slate-400 hover:text-rose-500 transition-colors flex items-center space-x-1">
                            <i class="fa-solid fa-trash-can text-[10px]"></i>
                            <span>清空卷面 (${cart.length})</span>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;

        if (currentTab === 'all' && pagination.all.loading) {
            html += `
                <div class="ui-state ui-state-loading min-h-[220px] rounded-2xl border border-slate-200/80 bg-white/60 dark:border-slate-700 dark:bg-slate-800/50" role="status" aria-live="polite">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-spinner fa-spin"></i></span>
                    <strong class="ui-state-title">正在加载题库第 ${pagination.all.retryPage || 1} 页</strong>
                    <span class="ui-state-description">正在读取当前筛选条件下的题目资源。</span>
                </div>
            `;
            container.innerHTML = html;
            return;
        }

        if (currentTab === 'all' && pagination.all.error) {
            html += `
                <div class="ui-state ui-state-error min-h-[220px] rounded-2xl border border-rose-200 bg-rose-50/60 px-5 dark:border-rose-900/60 dark:bg-rose-950/30" role="alert">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-circle-exclamation"></i></span>
                    <strong class="ui-state-title">${escapeHtml(pagination.all.error)}</strong>
                    <span class="ui-state-description">${Number.isInteger(pagination.all.total)
                        ? `上次成功加载时共 ${pagination.all.total} 题，本次请求尚未完成。`
                        : '当前筛选结果总数尚未确认。'}</span>
                    <button type="button" onclick="window.retryPaperBankQuestions()" class="ui-state-action">重新加载</button>
                </div>
            `;
            container.innerHTML = html;
            return;
        }

        if (currentTab === 'selected' && cartLoadState.error) {
            html += `
                <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200" role="alert">
                    <span><i class="fa-solid fa-triangle-exclamation mr-1.5"></i>${escapeHtml(cartLoadState.error)}</span>
                    <span class="flex flex-wrap items-center gap-2">
                        <button type="button" onclick="window.retryPaperCartQuestions()" class="rounded-lg border border-amber-300 bg-white px-3 py-1.5 font-semibold hover:bg-amber-100 dark:border-amber-800 dark:bg-slate-800 dark:hover:bg-slate-700">重新加载</button>
                        ${(cartLoadState.confirmedMissingIds || []).length > 0 ? `
                            <button type="button" onclick="window.removeMissingPaperCartQuestions()" class="rounded-lg border border-rose-300 bg-white px-3 py-1.5 font-semibold text-rose-600 hover:bg-rose-50 dark:border-rose-800 dark:bg-slate-800 dark:text-rose-300 dark:hover:bg-slate-700">只移除确认失效题</button>
                        ` : ''}
                    </span>
                </div>
            `;
        } else if (currentTab === 'selected' && cartLoadState.loading) {
            html += `
                <div class="mb-4 flex items-center gap-2 rounded-xl border border-brand-200 bg-brand-50/60 px-4 py-3 text-xs text-brand-700 dark:border-brand-900/60 dark:bg-brand-900/30 dark:text-brand-200" role="status" aria-live="polite">
                    <i class="fa-solid fa-spinner fa-spin"></i><span>正在加载已选题目...</span>
                </div>
            `;
        }

        if (displayList.length === 0) {
            const emptyTitle = currentTab === 'selected'
                ? (cart.length > 0 ? '已选题目暂未载入' : '尚未选择试题')
                : '未找到符合条件的题目';
            const emptyDescription = currentTab === 'selected'
                ? (cart.length > 0 ? '请重新加载已选题目后再预览或导出试卷。' : '从“全库试题”中加入题目后，会在这里按卷面顺序显示。')
                : '请在上方调节学段、章节、题型、难度或搜索条件。';
            html += `
                <div class="ui-state ui-state-empty py-20 bg-white/50 backdrop-blur-md rounded-2xl border border-dashed border-slate-300 dark:bg-slate-800/40 dark:border-slate-700">
                    <div class="ui-state-icon w-12 h-12 rounded-2xl bg-brand-50 text-brand-500 flex items-center justify-center text-xl dark:bg-slate-800">
                        <i class="fa-solid fa-folder-open"></i>
                    </div>
                    <h4 class="ui-state-title font-semibold text-slate-700 dark:text-slate-200">${emptyTitle}</h4>
                    <p class="ui-state-description text-xs text-slate-500 max-w-xs text-center">${emptyDescription}</p>
                </div>
            `;
            html += renderPaperStreamPagination(currentTab, currentPage, total, totalPages);
            container.innerHTML = html;
            return;
        }

        html += `<div class="space-y-4">`;

        displayEntries.forEach((entry) => {
            const q = entry.question;
            const cartIndex = entry.cartIndex;
            const inCart = window.isInCart(q.id);
            const cartItem = cart.find(it => it.id === q.id);
            const currentScore = cartItem ? cartItem.score : (q.question_type === 'detailed_answer' ? 12 : 5);
            const qTypeLabel = getQuestionTypeCn(q.question_type);
            const diffTag = getDifficultyBadge(q.difficulty);
            const usageCount = q.usage_count || 0;
            seedPaperAnswerCache(q);
            const answerExpanded = window.PaperStore.expandedAnswerIds.has(q.id);
            const answerLoading = window.PaperStore.answerLoadingIds.has(q.id);
            const answerError = window.PaperStore.answerErrors[q.id] || '';
            const answerCached = hasCachedPaperAnswer(q.id);
            const answerText = answerCached ? window.PaperStore.answerCache[q.id] : '';
            const answerAvailabilityKnown = typeof q.has_answer === 'boolean' || answerCached;
            const hasAnswer = Boolean((answerText || '').trim()) || q.has_answer === true || !answerAvailabilityKnown;

            let answerBodyHtml = '';
            if (answerExpanded) {
                if (answerLoading) {
                    answerBodyHtml = `
                        <div class="flex items-center justify-center gap-2 py-5 text-xs text-slate-500 dark:text-slate-400" aria-live="polite">
                            <i class="fa-solid fa-spinner fa-spin text-brand-500"></i>
                            <span>正在加载答案与解析...</span>
                        </div>
                    `;
                } else if (answerError) {
                    answerBodyHtml = `
                        <div class="flex flex-wrap items-center justify-between gap-2 py-3 text-xs text-rose-600 dark:text-rose-300" role="alert">
                            <span><i class="fa-solid fa-circle-exclamation mr-1"></i>${escapeHtml(answerError)}</span>
                            <button type="button" onclick="window.retryPaperQuestionAnswer(${q.id})"
                                class="px-3 py-1.5 rounded-lg border border-rose-200 bg-white hover:bg-rose-50 font-semibold transition-colors dark:bg-slate-800 dark:border-rose-900/60 dark:hover:bg-slate-700">
                                重试
                            </button>
                        </div>
                    `;
                } else if ((answerText || '').trim()) {
                    answerBodyHtml = typeof window.parseMarkdownWithMath === 'function'
                        ? window.parseMarkdownWithMath(answerText)
                        : window.MathBankSafe.sanitizeRichHtml(answerText);
                } else {
                    answerBodyHtml = '<p class="py-3 text-xs text-slate-400 italic">本题暂无答案与解析。</p>';
                }
            }

            const cardBorderClass = inCart 
                ? 'border-brand-500 ring-2 ring-brand-500/20 bg-brand-50/10 dark:border-brand-500/60 dark:bg-brand-900/20'
                : 'border-slate-200/80 hover:border-brand-200/80 bg-white/80 dark:bg-slate-800/80 dark:border-slate-700/70';

            html += `
                <div class="p-5 rounded-2xl border ${cardBorderClass} shadow-sm hover:shadow-md transition-all">
                    <!-- Card Top Controls Bar -->
                    <div class="flex flex-col gap-3 pb-3 mb-3 border-b border-slate-100 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700/60">
                        <div class="flex w-full items-center space-x-2 flex-wrap gap-y-1 sm:w-auto">
                            <span class="font-bold text-slate-800 dark:text-slate-100 text-sm">#${escapeHtml(q.seq_num !== undefined ? q.seq_num : q.id)}</span>
                            <span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-brand-50 text-brand-600 border border-brand-200/50 dark:bg-brand-900/30 dark:text-brand-200 dark:border-brand-900/50">${escapeHtml(qTypeLabel)}</span>
                            ${diffTag}
                            ${q.category_compulsory ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">${escapeHtml(q.category_compulsory)}</span>` : ''}
                            ${q.category_chapter ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">${escapeHtml(q.category_chapter)}</span>` : ''}
                            <span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400" title="引用次数">引用 ${escapeHtml(usageCount)} 次</span>
                        </div>

                        <div class="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
                            <button type="button"
                                onclick="window.togglePaperQuestionAnswer(${q.id})"
                                aria-expanded="${answerExpanded ? 'true' : 'false'}"
                                ${answerExpanded ? `aria-controls="paper-q-answer-${q.id}"` : ''}
                                ${(!hasAnswer && !answerExpanded) || answerLoading ? 'disabled' : ''}
                                title="${hasAnswer || answerExpanded ? (answerExpanded ? '收起本题答案与解析' : '查看本题答案与解析') : '本题暂无答案'}"
                                class="min-h-[44px] sm:min-h-[32px] px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all flex items-center justify-center space-x-1.5
                                    ${answerExpanded
                                        ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200'
                                        : 'border-slate-200 bg-white/80 text-slate-600 hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-brand-700'}
                                    disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-slate-200 disabled:hover:bg-white/80 disabled:hover:text-slate-600">
                                <i class="fa-solid ${answerLoading ? 'fa-spinner fa-spin' : (answerExpanded ? 'fa-eye-slash' : 'fa-eye')} text-[11px]"></i>
                                <span>${answerLoading ? '加载中' : (answerExpanded ? '收起答案' : (hasAnswer ? '查看答案' : '暂无答案'))}</span>
                            </button>

                            ${inCart ? `
                                <!-- Score Selector -->
                                <div class="paper-score-pill flex items-center space-x-1 px-2.5 py-1 rounded-xl">
                                    <span class="text-xs font-medium">分值:</span>
                                    <input type="number" min="1" max="100" value="${currentScore}" 
                                        onchange="window.updatePaperQuestionScore(${q.id}, this.value)"
                                        class="w-12 text-center text-xs font-bold rounded-lg focus:outline-none">
                                    <span class="text-xs font-medium">分</span>
                                </div>

                                <!-- Move Up / Move Down -->
                                ${currentTab === 'selected' ? `
                                    <button onclick="window.movePaperQuestion(${cartIndex}, 'up')" ${cartIndex === 0 ? 'disabled' : ''}
                                        class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="上移">
                                        <i class="fa-solid fa-arrow-up text-xs"></i>
                                    </button>
                                    <button onclick="window.movePaperQuestion(${cartIndex}, 'down')" ${cartIndex === cart.length - 1 ? 'disabled' : ''}
                                        class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="下移">
                                        <i class="fa-solid fa-arrow-down text-xs"></i>
                                    </button>
                                ` : ''}

                                <!-- Remove Button -->
                                <button onclick="window.removeFromCart(${q.id})" 
                                    class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-white shadow-sm hover:bg-rose-600 active:scale-95 transition-all flex items-center space-x-1" title="点击移出试卷">
                                    <i class="fa-solid fa-check text-xs"></i>
                                    <span>已入卷</span>
                                </button>
                            ` : `
                                <!-- Add Button -->
                                <button onclick="window.addToCart(${q.id})" 
                                    class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1">
                                    <i class="fa-solid fa-plus text-xs"></i>
                                    <span>加入试卷</span>
                                </button>
                            `}
                        </div>
                    </div>

                    <!-- Full Question Render Content -->
                    <div class="question-full-render-box text-sm leading-relaxed text-slate-800 dark:text-slate-100 overflow-x-auto select-text" id="paper-q-render-${q.id}">
                        ${formatQuestionContentHtml(q.content, q.id, getQuestionFigAlign(q), false, false, getQuestionFigSize(q), q.image_layouts || {})}
                    </div>

                    ${answerExpanded ? `
                        <section id="paper-q-answer-${q.id}"
                            role="region"
                            aria-label="题目 #${escapeHtml(q.seq_num !== undefined ? q.seq_num : q.id)} 的参考答案与解析"
                            class="mt-4 pt-4 border-t border-dashed border-brand-200/80 dark:border-brand-900/70">
                            <div class="mb-2 flex items-center gap-2 text-xs font-bold text-brand-700 dark:text-brand-200">
                                <i class="fa-solid fa-signature"></i>
                                <span>参考答案与解析</span>
                            </div>
                            <div class="paper-answer-render-box rounded-xl border border-brand-100 bg-brand-50/40 px-4 py-3 text-sm leading-relaxed text-slate-800 overflow-x-auto select-text dark:border-brand-900/60 dark:bg-brand-900/20 dark:text-slate-100">
                                <div id="paper-q-answer-content-${q.id}">${answerBodyHtml}</div>
                            </div>
                        </section>
                    ` : ''}
                </div>
            `;
        });

        html += `</div>`;
        html += renderPaperStreamPagination(currentTab, currentPage, total, totalPages);
        container.innerHTML = html;

        // Render math formulas for Part 3 question cards
        displayList.forEach(q => {
            const el = document.getElementById(`paper-q-render-${q.id}`);
            if (el && typeof renderMathInElement === 'function') {
                try {
                    renderMathInElement(el, {
                        delimiters: [
                            { left: '$$', right: '$$', display: true },
                            { left: '$', right: '$', display: false },
                            { left: '\\(', right: '\\)', display: false },
                            { left: '\\[', right: '\\]', display: true }
                        ],
                        throwOnError: false
                    });
                    if (typeof window.adaptChoicesGridLayout === 'function') {
                        window.adaptChoicesGridLayout(el);
                    }
                } catch (e) { }
            }

            const answerEl = document.getElementById(`paper-q-answer-content-${q.id}`);
            if (answerEl && !window.PaperStore.answerLoadingIds.has(q.id) && !window.PaperStore.answerErrors[q.id] && typeof renderMathInElement === 'function') {
                try {
                    renderMathInElement(answerEl, {
                        delimiters: [
                            { left: '$$', right: '$$', display: true },
                            { left: '$', right: '$', display: false },
                            { left: '\\(', right: '\\)', display: false },
                            { left: '\\[', right: '\\]', display: true }
                        ],
                        throwOnError: false
                    });
                } catch (e) { }
            }
        });
        initializeAutoFigureSizing(container);
    }

    // Render Part 4: Right A4 Canvas & Action Bar
    window.renderPaperCanvas = function () {
        const container = document.getElementById('paperCanvasSection');
        if (!container) return;

        if (window.activeA4PaginationResizeObserver) {
            window.activeA4PaginationResizeObserver.disconnect();
            window.activeA4PaginationResizeObserver = null;
        }
        if (window.activeA4PaginationFrame && typeof cancelAnimationFrame === 'function') {
            cancelAnimationFrame(window.activeA4PaginationFrame);
        }
        window.activeA4PaginationFrame = null;
        window.scheduleActiveA4Repagination = null;

        // 保存更新前的 A4 画布与外层 Section 滚动位置，解决重绘导致的视口跳回第一页问题
        const oldSheet = document.getElementById('a4PaperPreviewSheet');
        const savedSheetScrollTop = oldSheet ? oldSheet.scrollTop : 0;
        const savedContainerScrollTop = container ? container.scrollTop : 0;

        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;
        const missingCartQuestionIds = getMissingCartQuestionIds();
        const cartLoadState = window.PaperStore.cartQuestionLoad;
        const unavailableCartQuestionIds = Array.from(new Set([
            ...missingCartQuestionIds,
            ...(cartLoadState.confirmedMissingIds || []),
            ...(cartLoadState.failedIds || [])
        ].map(qid => parseInt(qid, 10)).filter(qid => qid > 0)));
        const cartIncomplete = Boolean(cartLoadState.loading)
            || unavailableCartQuestionIds.length > 0;

        const validCartStats = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const totalScore = validCartStats.reduce((sum, item) => sum + (parseInt(item.score, 10) || 5), 0);
        const totalCount = validCartStats.length;

        // Calculate difficulty ratio
        let easyCount = 0, medCount = 0, hardCount = 0;
        validCartStats.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (q) {
                if (q.difficulty === 'easy' || q.difficulty === 'normal') easyCount++;
                else if (q.difficulty === 'hard' || q.difficulty === 'qiangji') hardCount++;
                else medCount++;
            }
        });
        const easyPct = totalCount > 0 ? Math.round((easyCount / totalCount) * 100) : 0;
        const medPct = totalCount > 0 ? Math.round((medCount / totalCount) * 100) : 0;
        const hardPct = totalCount > 0 ? Math.max(0, 100 - easyPct - medPct) : 0;

        let aiAnalysisBanner = '';
        if (meta.ai_analysis) {
            aiAnalysisBanner = `
                <div class="mb-4 p-3.5 rounded-2xl border border-brand-200/80 bg-brand-50/50 backdrop-blur-md shadow-sm dark:bg-brand-900/40 dark:border-brand-900 transition-all">
                    <div class="flex items-center justify-between mb-1.5 pb-1 border-b border-brand-200/50 dark:border-brand-900/60">
                        <div class="flex items-center space-x-1.5 text-xs font-bold text-brand-700 dark:text-brand-200">
                            <i class="fa-solid fa-brain text-brand-500"></i>
                            <span>双向细目表与考点覆盖分析 (${escapeHtml(meta.ai_model_used || '大模型')})</span>
                        </div>
                        <button onclick="window.clearAiAnalysis()" class="text-[10px] text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 px-1.5 py-0.5 rounded-md hover:bg-slate-200/60 font-medium transition-all" title="关闭分析框">
                            <i class="fa-solid fa-xmark mr-1"></i>关闭分析
                        </button>
                    </div>
                    <div class="text-xs text-slate-700 dark:text-slate-300 whitespace-pre-wrap leading-relaxed">
                        ${escapeHtml(meta.ai_analysis)}
                    </div>
                </div>
            `;
        }

        const cartLoadBanner = cartIncomplete ? `
            <div class="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200" role="alert">
                <span><i class="fa-solid fa-triangle-exclamation mr-1.5"></i>${cartLoadState.loading
                    ? '正在向服务端核验完整卷面，预览、保存与导出已暂停。'
                    : `${escapeHtml(cartLoadState.error || `有 ${unavailableCartQuestionIds.length} 道已选题目尚未完成核验。`)} 卷面预览、保存与导出已暂停。`}</span>
                <button type="button" onclick="window.retryPaperCartQuestions()" class="rounded-lg border border-amber-300 bg-white px-3 py-1.5 font-semibold hover:bg-amber-100 dark:border-amber-800 dark:bg-slate-800 dark:hover:bg-slate-700">重新加载</button>
            </div>
        ` : '';

        container.innerHTML = `
            ${aiAnalysisBanner}
            ${cartLoadBanner}
            <!-- Part 1: Top Fixed Control Section (Non-scrolling Studio Panel) -->
            <div class="shrink-0 mb-3">
                <div class="bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl flex flex-col space-y-2.5 shadow-sm">
                    <!-- Row 1: Header Stats & Solution Space Config -->
                    <div class="flex items-center justify-between flex-wrap gap-2 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        <div class="flex items-center space-x-3">
                            <div class="flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-brand-50 text-brand-700 font-bold text-xs border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">
                                <span>${cartIncomplete ? '已加载总分' : '总分'}: ${totalScore} 分</span>
                                <span class="text-slate-400 font-normal">|</span>
                                <span>${cartIncomplete ? `已加载 ${totalCount} / 已选 ${cart.length} 题` : `${totalCount} 题`}</span>
                            </div>
                            <!-- Difficulty ratio bar -->
                            <div class="hidden xl:flex items-center space-x-1.5 text-xs">
                                <span class="text-slate-400 font-medium">难度比:</span>
                                <div class="w-20 h-2 rounded-full bg-slate-200 overflow-hidden flex dark:bg-slate-700" title="普通题: ${easyPct}% | 挑战题: ${medPct}% | 强基题: ${hardPct}%">
                                    <div class="bg-emerald-500 h-full" style="width: ${easyPct}%"></div>
                                    <div class="bg-amber-500 h-full" style="width: ${medPct}%"></div>
                                    <div class="bg-rose-500 h-full" style="width: ${hardPct}%"></div>
                                </div>
                            </div>
                            <!-- Solution Space Selector -->
                            <div class="flex items-center space-x-1 text-xs">
                                <span class="text-slate-500 font-semibold dark:text-slate-300 flex items-center space-x-1">
                                    <i class="fa-solid fa-arrows-up-down text-brand-500"></i>
                                    <span>留白:</span>
                                </span>
                                <select onchange="window.updateGlobalSolutionSpace(this.value)"
                                    class="px-2 py-1 text-xs rounded-xl border border-brand-200/80 bg-brand-50/60 text-brand-900 font-bold focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200">
                                    ${meta.paper_type === 'exam_19' ? `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 0.0) ? 'selected' : ''}>0 cm (不留白)</option>
                                        <option value="3.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 3.0) ? 'selected' : ''}>3 cm (紧凑留白)</option>
                                    ` : `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 0.0) ? 'selected' : ''}>0 cm (不留白)</option>
                                        <option value="7.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 7.0) ? 'selected' : ''}>7 cm (标准留白)</option>
                                    `}
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Row 2: Paper Management Actions (Clear, Save, History) -->
                    <div class="flex items-center justify-between gap-2.5 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        ${totalCount > 0 ? `
                            <button onclick="clearCart()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-rose-50/80 text-rose-600 border border-rose-200/70 hover:bg-rose-100/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-rose-950/40 dark:border-rose-800/60 dark:text-rose-300" title="清空当前试卷已选题目">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>清空卷面</span>
                            </button>
                        ` : ''}
                        <button onclick="savePaperToDb()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="保存当前试卷至本地数据库">
                            <i class="fa-solid fa-floppy-disk"></i>
                            <span>保存试卷</span>
                        </button>
                        <button onclick="openSavedPapersModal()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-amber-500 text-white shadow-sm hover:bg-amber-600 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="查阅与管理历史归档试卷">
                            <i class="fa-solid fa-folder-open"></i>
                            <span>历史试卷库</span>
                        </button>
                    </div>

                    <!-- Row 3: Preview & Export Options -->
                    <div class="flex flex-wrap items-center justify-between gap-2 sm:gap-2.5">
                        ${meta.paper_type === 'exam_19' ? `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>试卷 PDF 预览</span>
                            </button>
                            <button onclick="exportPaperPdf('sheet')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开 A3 双面答题卡 PDF 预览">
                                <i class="fa-solid fa-file-lines"></i>
                                <span>答题卡 PDF 预览</span>
                            </button>
                            <button onclick="exportPaperWord()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出可编辑 Word 试卷正文（不含答题卡）">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX 打包</span>
                            </button>
                        ` : `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>PDF 预览</span>
                            </button>
                            <button onclick="exportPaperWord()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出保留原生公式的可编辑 Word 试卷">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX 打包</span>
                            </button>
                        `}
                    </div>
                </div>
            </div>

            <!-- Part 2: Independent Scrollable A4 Desk Canvas Paper Container -->
            <div class="flex-1 overflow-y-auto custom-scrollbar pt-1 pb-10 flex flex-col items-center" id="a4PaperPreviewSheet">
                ${cartIncomplete ? `
                    <div class="m-auto max-w-sm rounded-2xl border border-dashed border-amber-300 bg-white/80 px-6 py-8 text-center text-amber-800 shadow-sm dark:border-amber-800 dark:bg-slate-900/70 dark:text-amber-200">
                        <i class="fa-solid fa-file-circle-exclamation mb-3 text-2xl"></i>
                        <p class="text-sm font-semibold">卷面题目尚未完整加载</p>
                        <p class="mt-1 text-xs opacity-80">重新加载成功后才会显示完整 A4 预览。</p>
                    </div>
                ` : generateA4PaperPagesHtml(cart, meta, totalCount, totalScore)}
            </div>
        `;

        // Render math in A4 sheet
        const sheet = document.getElementById('a4PaperPreviewSheet');
        if (sheet && typeof renderMathInElement === 'function') {
            try {
                renderMathInElement(sheet, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                        { left: '\\(', right: '\\)', display: false },
                        { left: '\\[', right: '\\]', display: true }
                    ],
                    throwOnError: false
                });
                if (typeof window.adaptChoicesGridLayout === 'function') {
                    window.adaptChoicesGridLayout(sheet);
                }
            } catch (e) { }
        }
        initializeAutoFigureSizing(sheet);
        if (sheet && !cartIncomplete) {
            rebalanceA4PaperPages(sheet, meta, totalCount, totalScore);

            let resizeObserver = null;
            const repaginateAfterLayoutChange = () => {
                if (!sheet.isConnected || window.scheduleActiveA4Repagination !== repaginateAfterLayoutChange) {
                    if (resizeObserver) resizeObserver.disconnect();
                    if (window.activeA4PaginationResizeObserver === resizeObserver) {
                        window.activeA4PaginationResizeObserver = null;
                    }
                    return;
                }
                // The drag placeholder changes block heights. Keep pages still
                // until drop/cancel removes it and renders the final cart.
                if (draggedItemData) return;
                if (window.activeA4PaginationFrame) return;
                if (typeof requestAnimationFrame === 'function') {
                    const frame = requestAnimationFrame(() => {
                        if (window.activeA4PaginationFrame === frame) window.activeA4PaginationFrame = null;
                        if (sheet.isConnected && !draggedItemData
                                && window.scheduleActiveA4Repagination === repaginateAfterLayoutChange) {
                            rebalanceA4PaperPages(sheet, meta, totalCount, totalScore);
                        }
                    });
                    window.activeA4PaginationFrame = frame;
                } else {
                    rebalanceA4PaperPages(sheet, meta, totalCount, totalScore);
                }
            };
            window.scheduleActiveA4Repagination = repaginateAfterLayoutChange;

            if (typeof ResizeObserver !== 'undefined') {
                resizeObserver = new ResizeObserver(repaginateAfterLayoutChange);
                resizeObserver.observe(sheet);
                sheet.querySelectorAll('.paper-page-block').forEach(block => resizeObserver.observe(block));
                const header = sheet.querySelector('.paper-page-header');
                if (header) resizeObserver.observe(header);
                window.activeA4PaginationResizeObserver = resizeObserver;
            }
            sheet.querySelectorAll('img').forEach(image => {
                if (!image.complete) {
                    image.addEventListener('load', repaginateAfterLayoutChange, { once: true });
                    image.addEventListener('error', repaginateAfterLayoutChange, { once: true });
                }
            });
            if (document.fonts && document.fonts.ready) {
                document.fonts.ready.then(repaginateAfterLayoutChange).catch(() => {});
            }
        }

        // 恢复更新前的滚动位置，保证调排版/留白/格式时在原视口位置零跳跃渲染
        const restoreScroll = () => {
            const newSheet = document.getElementById('a4PaperPreviewSheet');
            if (newSheet && savedSheetScrollTop > 0) {
                newSheet.scrollTop = savedSheetScrollTop;
            }
            const curContainer = document.getElementById('paperCanvasSection');
            if (curContainer && savedContainerScrollTop > 0) {
                curContainer.scrollTop = savedContainerScrollTop;
            }
        };

        restoreScroll();
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(restoreScroll);
        }
    };

    function renderA4Header(meta, totalCount, totalScore, totalPages) {
        const isExamType = (meta.paper_type === 'exam' || meta.paper_type === 'exam_19');
        return `
            <!-- Top Secret Mark Bar -->
            ${isExamType ? `
                ${meta.show_secret !== false ? `
                    <div class="group relative flex justify-between items-center mb-3 text-xs font-serif font-bold text-slate-800 pb-1 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 px-2 py-0.5 rounded-lg transition-all duration-200 cursor-default">
                        <span>绝密★启用前</span>
                        <button type="button" onclick="updatePaperMeta('show_secret', false)" 
                                title="点击移除绝密标记"
                                class="opacity-0 group-hover:opacity-100 absolute top-0.5 right-1 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除标记</span>
                        </button>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_secret', true)" 
                         title="点击恢复绝密标记"
                         class="mb-3 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-lg py-0.5 px-2 text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center space-x-1.5 w-fit">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-xs group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除绝密标记 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}

            <!-- Exam Header Title & Subject -->
            <div class="title-header-group text-center mb-3">
                <h1 contenteditable="true"
                    oninput="updatePaperMeta('title', this.innerText)"
                    onblur="saveMetaToStorage()"
                    title="点击直接在试卷上修改主标题"
                    placeholder="+ 点击在此直接添加主标题"
                    class="canvas-meta-title text-2xl font-bold tracking-normal text-slate-900 font-serif mb-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text inline-block min-w-[200px]"
                    spellcheck="false">${(meta.title && meta.title.trim()) ? escapeHtml(meta.title) : ''}</h1>
                <div class="text-xl font-bold text-slate-900 font-serif my-2 select-none">数 学</div>
                <div contenteditable="true"
                     oninput="updatePaperMeta('subtitle', this.innerText)"
                     onblur="saveMetaToStorage()"
                     title="点击直接在试卷上修改副标题/备注"
                     placeholder="+ 点击在此直接添加副标题 / 备注"
                     class="canvas-meta-subtitle text-sm font-bold font-serif text-slate-900 my-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text min-w-[140px] inline-block"
                     spellcheck="false">${(meta.subtitle && meta.subtitle.trim()) ? escapeHtml(meta.subtitle) : ''}</div>
            </div>

            ${isExamType ? `
                <div class="text-[12px] text-center font-serif text-slate-800 mb-4">
                    本试卷共 <span data-paper-total-pages>${totalPages}</span> 页，${totalCount} 题。全卷满分 ${totalScore} 分。考试用时 120 分钟。
                </div>

                <!-- Standard LaTeX Notice Block with Interactive Toggle -->
                ${meta.show_notice !== false ? `
                    <div class="group relative mb-5 text-[11.5px] leading-relaxed font-serif text-slate-800 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 p-2.5 rounded-xl transition-all duration-200 cursor-default">
                        <button type="button" onclick="updatePaperMeta('show_notice', false)" 
                                title="点击移除注意事项"
                                class="opacity-0 group-hover:opacity-100 absolute -top-2.5 right-2 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2.5 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除注意事项</span>
                        </button>
                        <div class="font-bold mb-1 text-slate-900 text-[12px]">注意事项：</div>
                        <ol class="list-decimal list-inside space-y-0.5 text-slate-800 pl-4">
                            <li>答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。</li>
                            <li>回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。</li>
                            <li>考试结束后，将本试卷和答题卡一并交回。</li>
                        </ol>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_notice', true)" 
                         title="点击恢复注意事项"
                         class="mb-4 my-2 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-xl p-2 text-center text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center justify-center space-x-1.5">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-sm group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除注意事项 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}
        `;
    }

    function paginatePaperBlocksByHeight(blocks, firstPageLimit, laterPageLimit) {
        const pages = [];
        let currentPage = [];
        let currentHeight = 0;

        blocks.forEach((block, index) => {
            const pageLimit = pages.length === 0 ? firstPageLimit : laterPageLimit;
            const nextBlock = blocks[index + 1];
            const keepWithNextHeight = (
                block.type === 'section_title'
                && nextBlock
                && nextBlock.type === 'question'
                && nextBlock.qType === block.qType
            ) ? block.height + nextBlock.height : block.height;
            const wouldOverflow = currentHeight + block.height > pageLimit;
            const wouldOrphanHeading = currentHeight + keepWithNextHeight > pageLimit;
            const headingPairFitsLaterPage = (
                block.type === 'section_title'
                && currentPage.length === 0
                && pages.length === 0
                && keepWithNextHeight > firstPageLimit
                && keepWithNextHeight <= laterPageLimit
            );
            if (headingPairFitsLaterPage) {
                pages.push([]);
            }
            const keepOversizeWithHeading = (
                block.type === 'question'
                && block.height > laterPageLimit
                && currentPage.length === 1
                && currentPage[0].type === 'section_title'
                && currentPage[0].qType === block.qType
            );

            if (currentPage.length > 0 && !keepOversizeWithHeading && (wouldOverflow || wouldOrphanHeading)) {
                pages.push(currentPage);
                currentPage = [];
                currentHeight = 0;
            }

            currentPage.push(block);
            currentHeight += block.height;
        });

        if (currentPage.length > 0) pages.push(currentPage);
        return pages;
    }
    window.paginatePaperBlocksByHeight = paginatePaperBlocksByHeight;

    function getPaperBlockOuterHeight(element) {
        const style = window.getComputedStyle(element);
        const marginTop = parseFloat(style.marginTop) || 0;
        const marginBottom = parseFloat(style.marginBottom) || 0;
        return Math.ceil(element.getBoundingClientRect().height + marginTop + marginBottom);
    }

    function createMeasuredA4Page(meta, totalCount, totalScore, pageIndex, totalPages, pageLimit, blocks) {
        const page = document.createElement('div');
        page.className = 'a4-paper-sheet w-full max-w-[794px] h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none mb-8';
        page.innerHTML = `
            ${pageIndex === 0 ? `<div class="paper-page-header">${renderA4Header(meta, totalCount, totalScore, totalPages)}</div>` : ''}
            <div class="paper-page-content space-y-1.5 text-[13px]"></div>
            <div class="paper-page-footer absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">
                数学 &nbsp; 第 ${pageIndex + 1} 页 (共 ${totalPages} 页)
            </div>
        `;
        updateMeasuredA4Page(page, pageIndex, totalPages, pageLimit, blocks);
        return page;
    }

    function updateMeasuredA4Page(page, pageIndex, totalPages, pageLimit, blocks) {
        const isExpanded = blocks.reduce((height, block) => height + block.height, 0) > pageLimit;
        page.classList.toggle('a4-paper-sheet--expanded', isExpanded);
        page.dataset.paperPageIndex = String(pageIndex);
        page.dataset.paperPageExpanded = isExpanded ? 'true' : 'false';
        let notice = page.querySelector('.paper-oversize-notice');
        if (isExpanded && !notice) {
            notice = document.createElement('div');
            notice.className = 'paper-oversize-notice';
            notice.setAttribute('role', 'status');
            notice.textContent = '本页题目超过单页高度，预览已自动扩展以完整显示；导出时由排版引擎继续分页。';
            page.insertBefore(notice, page.querySelector('.paper-page-content'));
        } else if (!isExpanded && notice) {
            notice.remove();
        }
        page.querySelector('.paper-page-footer').textContent = `数学　 第 ${pageIndex + 1} 页 (共 ${totalPages} 页)`;
    }

    function getA4PageHeightLimits(firstPage, firstContent, firstFooter) {
        const pageStyle = window.getComputedStyle(firstPage);
        const footerStyle = window.getComputedStyle(firstFooter);
        const pageRect = firstPage.getBoundingClientRect();
        // min-height stays at the standard A4 height even when an oversize page
        // expands. Never derive capacity from that expanded page's bottom.
        const standardHeight = parseFloat(pageStyle.minHeight) || 1123;
        const borderTop = parseFloat(pageStyle.borderTopWidth) || 0;
        const borderBottom = parseFloat(pageStyle.borderBottomWidth) || 0;
        const paddingTop = parseFloat(pageStyle.paddingTop) || 0;
        const footerTop = standardHeight - borderBottom - (parseFloat(footerStyle.bottom) || 0)
            - firstFooter.getBoundingClientRect().height;
        const notice = firstPage.querySelector('.paper-oversize-notice');
        const contentTop = firstContent.getBoundingClientRect().top - pageRect.top
            - (notice ? getPaperBlockOuterHeight(notice) : 0);
        const footerGap = 12;
        return {
            firstPageLimit: Math.max(0, Math.floor(footerTop - contentTop - footerGap)),
            laterPageLimit: Math.max(0, Math.floor(footerTop - borderTop - paddingTop - footerGap))
        };
    }

    function rebalanceA4PaperPages(sheet, meta, totalCount, totalScore) {
        if (!sheet || !sheet.querySelectorAll) return;
        const blockNodes = Array.from(sheet.querySelectorAll('.paper-page-block'));
        const firstPage = sheet.querySelector('.a4-paper-sheet');
        const firstContent = firstPage ? firstPage.querySelector('.paper-page-content') : null;
        const firstFooter = firstPage ? firstPage.querySelector('.paper-page-footer') : null;
        if (!blockNodes.length || !firstPage || !firstContent || !firstFooter) return;

        const { firstPageLimit, laterPageLimit } = getA4PageHeightLimits(firstPage, firstContent, firstFooter);
        const measuredBlocks = blockNodes.map(node => ({
            node,
            type: node.dataset.paperBlockType || 'question',
            qType: node.dataset.paperBlockQtype || '',
            height: getPaperBlockOuterHeight(node)
        }));
        const pages = paginatePaperBlocksByHeight(measuredBlocks, firstPageLimit, laterPageLimit);
        if (!pages.length) return;

        const savedScrollTop = sheet.scrollTop;
        const oldPages = Array.from(sheet.querySelectorAll(':scope > .a4-paper-sheet'));
        const existingHeader = firstPage.querySelector('.paper-page-header');
        const newPages = [];
        pages.forEach((blocks, pageIndex) => {
            const pageLimit = pageIndex === 0 ? firstPageLimit : laterPageLimit;
            // Retain existing pages, especially the first page and its editable
            // header: even reparenting the same header node loses focus/IME.
            let page = oldPages[pageIndex];
            if (page) {
                updateMeasuredA4Page(page, pageIndex, pages.length, pageLimit, blocks);
            } else {
                page = createMeasuredA4Page(meta, totalCount, totalScore, pageIndex, pages.length, pageLimit, blocks);
                sheet.appendChild(page);
            }
            newPages.push(page);
        });

        if (existingHeader) {
            const totalPagesNode = existingHeader.querySelector('[data-paper-total-pages]');
            if (totalPagesNode && totalPagesNode.textContent !== String(pages.length)) {
                totalPagesNode.textContent = String(pages.length);
            }
        }

        pages.forEach((blocks, pageIndex) => {
            const content = newPages[pageIndex].querySelector('.paper-page-content');
            const pageLimit = pageIndex === 0 ? firstPageLimit : laterPageLimit;
            let nextNode = content.firstElementChild;
            blocks.forEach(block => {
                block.node.classList.toggle('paper-page-block--oversize', block.height > pageLimit);
                if (block.node !== nextNode) content.insertBefore(block.node, nextNode);
                nextNode = block.node.nextElementSibling;
            });
        });
        oldPages.slice(pages.length).forEach(page => page.remove());
        sheet.scrollTop = savedScrollTop;
    }
    window.rebalanceA4PaperPages = rebalanceA4PaperPages;

    function generateA4PaperPagesHtml(cart, meta, totalCount, totalScore) {
        if (cart.length === 0) {
            return `
                <div class="a4-paper-sheet w-full max-w-[794px] h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none">
                    ${renderA4Header(meta, totalCount, totalScore, 1)}
                    <div class="text-center py-24 text-slate-400 font-sans text-xs">暂无试题数据，请在左侧点击“加入试卷”添加题目</div>
                    <div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">数学 &nbsp; 第 1 页 (共 1 页)</div>
                </div>
            `;
        }

        const validCart = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const cartItemsWithIndex = validCart.map((item, idx) => ({ ...item, cartIndex: idx }));

        const grouped = Object.create(null);

        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            const qType = q.question_type || 'single_choice';
            if (!grouped[qType]) grouped[qType] = [];
            grouped[qType].push(item);
        });

        const presentTypes = cartItemsWithIndex.map(item => window.PaperStore.questionsMap[item.id].question_type || 'single_choice');
        const typeOrder = getPaperTypeOrder(presentTypes, meta.paper_type, meta.section_order).filter(type => grouped[type] && grouped[type].length);
        const blocks = [];
        const secNums = ['一', '二', '三', '四', '五'];
        let secIdx = 0;
        const isExam19 = (meta.paper_type === 'exam_19');
        let globalQIndex = 1;

        typeOrder.forEach((qType, sectionIndex) => {
            const items = grouped[qType];
            if (!items || items.length === 0) return;

            // For exam_19: set fixed starting question number according to Gaokao rules
            if (isExam19) {
                if (qType === 'single_choice') globalQIndex = 1;
                else if (qType === 'multi_choice') globalQIndex = 9;
                else if (qType === 'fill_in_blank') globalQIndex = 12;
                else if (qType === 'detailed_answer') globalQIndex = 15;
            }

            const secNum = secNums[secIdx] || (secIdx + 1);
            secIdx++;

            const writtenType = isWrittenQuestionType(qType);
            const typeLabel = escapeHtml(getQuestionTypeCn(qType));
            const safeType = escapeHtml(qType);
            const count = items.length;
            const secScore = items.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);
            const unitScore = items[0] ? (parseInt(items[0].score, 10) || 5) : 5;

            let secHeaderText = '';
            if (meta.paper_type === 'quiz') {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、单选题`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题`;
                } else {
                    secHeaderText = `${secNum}、${qType === 'detailed_answer' ? '解答题' : typeLabel}`;
                }
            } else {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
                } else {
                    secHeaderText = `${secNum}、${qType === 'detailed_answer' ? '解答题' : typeLabel}：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
                }
            }

            blocks.push({
                type: 'section_title',
                qType: qType,
                html: `
                    <div class="paper-sec-block mb-3 flex items-start gap-3" data-qtype="${safeType}">
                        <h3 class="flex-1 min-w-0 font-bold text-[13.5px] font-serif mt-2 mb-2 text-slate-900 leading-snug">${secHeaderText}</h3>
                        ${isExam19 ? '' : `
                        <div class="paper-section-controls flex shrink-0 gap-1 print:hidden" aria-label="${typeLabel}大题排序">
                            <button type="button" data-section-move="up" onclick="window.movePaperSection(this.closest('[data-qtype]').dataset.qtype, 'up')" ${sectionIndex === 0 ? 'disabled' : ''}
                                class="min-h-[44px] min-w-[44px] px-2 rounded-lg text-xs font-sans text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default" aria-label="上移${typeLabel}大题" title="上移整个大题">↑ 上移</button>
                            <button type="button" data-section-move="down" onclick="window.movePaperSection(this.closest('[data-qtype]').dataset.qtype, 'down')" ${sectionIndex === typeOrder.length - 1 ? 'disabled' : ''}
                                class="min-h-[44px] min-w-[44px] px-2 rounded-lg text-xs font-sans text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:cursor-default" aria-label="下移${typeLabel}大题" title="下移整个大题">↓ 下移</button>
                        </div>`}
                    </div>
                `
            });

            items.forEach((item, subIdx) => {
                const q = window.PaperStore.questionsMap[item.id];
                let rawContent = q ? q.content : '';
                const figAlign = getQuestionFigAlign(q);
                const figSize = getQuestionFigSize(q);
                const figureMetrics = getDetachedFigureMetrics(rawContent, figAlign, figSize);
                const renderedFigAlign = figureMetrics.effectiveAlign;

                let solSpaceCm = 0;
                let isSolSpaceEmbedded = false;

                if (writtenType) {
                    const defaultFallback = meta.paper_type === 'exam_19' ? '0.0' : '7.0';
                    const defaultSpace = parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : defaultFallback);
                    solSpaceCm = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
                    if (isNaN(solSpaceCm)) solSpaceCm = 0.0;

                    if (solSpaceCm > 0 && ['bottom_left', 'center', 'bottom_right'].includes(renderedFigAlign)) {
                        isSolSpaceEmbedded = true;
                    }
                }

                const isChoiceQuestion = qType === 'single_choice' || qType === 'multi_choice';
                const choiceContentParts = isChoiceQuestion
                    ? splitChoiceContentForPaperPreview(rawContent)
                    : { stemRaw: rawContent, choicesRaw: '' };
                let contentRes = q ? formatQuestionContentHtml(choiceContentParts.stemRaw, q.id, figAlign, isSolSpaceEmbedded, true, figSize, q.image_layouts || {}) : '';
                const separatedChoicesHtml = q && choiceContentParts.choicesRaw
                    ? formatQuestionContentHtml(choiceContentParts.choicesRaw, q.id, figAlign, false, false, figSize, q.image_layouts || {})
                    : '';
                let contentHtml = '';
                let embeddedImgHtml = '';

                if (isSolSpaceEmbedded && typeof contentRes === 'object') {
                    contentHtml = contentRes.stemHtml;
                    embeddedImgHtml = contentRes.imgHtml || '';
                } else {
                    contentHtml = typeof contentRes === 'string' ? contentRes : (contentRes.stemHtml || '');
                }

                let stemLine = '';
                if (isChoiceQuestion) {
                    let stemContent = contentHtml;
                    let choicesGrid = separatedChoicesHtml;
                    if (!choicesGrid && (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid') || contentHtml.includes('grid-cols-'))) {
                        const match = contentHtml.match(/([\s\S]*?)(<(?:div|p)[^>]*class="[^"]*(?:choices-grid|katex-choices-grid|grid-cols-[124])"[\s\S]*)/i);
                        if (match) {
                            stemContent = match[1];
                            choicesGrid = match[2];
                        }
                    }
                    if (typeof window.cleanChoiceStemParentheses === 'function') {
                        stemContent = window.cleanChoiceStemParentheses(stemContent);
                    } else {
                        stemContent = stemContent.replace(/(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*)+$/, '').replace(/\\paren\b/g, '').trim();
                    }

                    stemLine = `
                        <div class="paper-choice-stem-row flex justify-between items-baseline mb-1">
                            <div class="flex-1">${stemContent}</div>
                            <div class="shrink-0 ml-4 font-serif text-slate-900 font-normal select-none">（ &nbsp; ）</div>
                        </div>
                        <div class="paper-choice-options-row">${choicesGrid}</div>
                    `;
                } else {
                    stemLine = contentHtml;
                }

                let solutionBlankHtml = '';
                if (writtenType) {
                    const spacePx = Math.round(solSpaceCm * 35);
                    const isZero = solSpaceCm <= 0;

                    let embeddedImgContainer = '';
                    if (isSolSpaceEmbedded && embeddedImgHtml) {
                        const posClass = renderedFigAlign === 'center'
                            ? 'left-1/2 -translate-x-1/2'
                            : (renderedFigAlign === 'bottom_left' ? 'left-3' : 'right-3');
                        embeddedImgContainer = `
                            <div class="absolute ${posClass} top-2 z-10">
                                ${embeddedImgHtml}
                            </div>
                        `;
                    }

                    const embeddedFigureHeight = figureMetrics.count > 0
                        ? Math.max(180, figureMetrics.blockHeight)
                        : 180;
                    const minHeightStyle = (isSolSpaceEmbedded && embeddedImgHtml)
                        ? `min-height: ${Math.max(spacePx, embeddedFigureHeight)}px; height: ${Math.max(spacePx, embeddedFigureHeight)}px;`
                        : (isZero ? 'min-height: 20px;' : `height: ${spacePx}px;`);

                    solutionBlankHtml = `
                        <div class="solution-space-zone group/blank relative mt-2 mb-1 ${isZero ? 'py-1 border-b border-dashed border-slate-200 hover:border-sky-300' : 'rounded-lg border border-dashed border-sky-300/80 bg-sky-50/20'} transition-all" data-solution-space-zone-qid="${q ? q.id : 0}" style="${minHeightStyle}">
                            ${embeddedImgContainer}
                            <!-- Anchor controls to the zone's top edge. They stay out
                                 of document flow, so preview pagination remains stable. -->
                            <div class="solution-space-controls absolute right-2 top-2 opacity-0 group-hover/blank:opacity-100 focus-within:opacity-100 transition-opacity flex items-center space-x-1 whitespace-nowrap bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-sm text-[10px] font-sans select-none z-20" data-solution-space-controls-qid="${q ? q.id : 0}">
                                <span class="text-slate-400 mr-1 font-medium">留白微调:</span>
                                <button type="button" data-solution-space-delta="-1" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 1cm 留白">
                                    - 1cm
                                </button>
                                <button type="button" data-solution-space-delta="-0.5" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 0.5cm 留白">
                                    - 0.5
                                </button>
                                <span class="inline-flex w-14 justify-center px-1 font-bold text-brand-600" data-solution-space-value="${q ? q.id : 0}">${solSpaceCm.toFixed(1)} cm</span>
                                <button type="button" data-solution-space-delta="0.5" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 0.5cm 留白">
                                    + 0.5
                                </button>
                                <button type="button" data-solution-space-delta="1" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 1cm 留白">
                                    + 1cm
                                </button>
                            </div>
                            <div class="absolute inset-0 flex items-center justify-center pointer-events-none ${isZero ? 'opacity-0 group-hover/blank:opacity-70' : 'opacity-40 group-hover/blank:opacity-80'} transition-opacity">
                                <span class="text-[10px] font-sans text-sky-700 font-medium tracking-wider select-none">
                                    <i class="fa-solid fa-pen-ruler mr-1"></i> ${typeLabel}留白区域 (${solSpaceCm.toFixed(1)} cm)
                                </span>
                            </div>
                        </div>
                    `;
                }

                const itemHtml = `
                    <div class="paper-q-item group relative text-[13px] leading-normal font-serif p-2 rounded-xl border border-transparent hover:border-brand-200 hover:bg-brand-50/30 transition-all duration-200 cursor-grab active:cursor-grabbing mb-2"
                        draggable="true"
                        data-qid="${q ? q.id : ''}"
                        data-qtype="${safeType}"
                        data-sub-index="${subIdx}"
                        ondragstart="onPaperCanvasDragStart(event, ${q ? q.id : 0}, ${subIdx}, this.dataset.qtype)"
                        ondragover="onPaperCanvasDragOver(event)"
                        ondragenter="onPaperCanvasDragEnter(event)"
                        ondragleave="onPaperCanvasDragLeave(event)"
                        ondragend="onPaperCanvasDragEnd(event)"
                        ondrop="onPaperCanvasDrop(event)">

                        <!-- Hover Action Bar: Drag Handle & Quick Move/Remove Buttons -->
                        <div class="paper-canvas-toolbar absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-sans select-none z-10">
                            <span class="toolbar-label font-medium mr-0.5"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType(this.closest('[data-qtype]').dataset.qtype, ${subIdx}, 'up')" ${subIdx === 0 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType(this.closest('[data-qtype]').dataset.qtype, ${subIdx}, 'down')" ${subIdx === items.length - 1 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="下移">
                                <i class="fa-solid fa-chevron-down"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.removeFromCart(${q ? q.id : 0})" class="toolbar-btn p-0.5 hover:text-rose-500" title="移出试卷">
                                <i class="fa-solid fa-xmark"></i>
                            </button>
                        </div>

                        <div class="flex items-baseline">
                            <span class="font-bold mr-1 text-slate-900 shrink-0">${globalQIndex}.</span>
                            <div class="inline flex-1">
                                ${stemLine}
                                ${solutionBlankHtml}
                            </div>
                        </div>
                    </div>
                `;

                blocks.push({
                    type: 'question',
                    qType: qType,
                    html: itemHtml
                });

                globalQIndex++;
            });
        });

        const initialContent = blocks.map((block, index) => `
            <div class="paper-page-block flow-root" data-paper-block-index="${index}" data-paper-block-type="${block.type}" data-paper-block-qtype="${escapeHtml(block.qType || '')}">
                ${block.html}
            </div>
        `).join('');

        // Render all blocks once at the exact A4 width. After KaTeX and choice
        // layout finish, rebalanceA4PaperPages measures these DOM nodes and
        // replaces this provisional page with the real page set.
        return `
            <div class="a4-paper-sheet w-full max-w-[794px] h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none mb-8" data-paper-page-index="0">
                <div class="paper-page-header">${renderA4Header(meta, totalCount, totalScore, 1)}</div>
                <div class="paper-page-content space-y-1.5 text-[13px]">
                    ${initialContent}
                </div>
                <div class="paper-page-footer absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">
                    数学 &nbsp; 第 1 页 (共 1 页)
                </div>
            </div>
        `;
    }

    window.movePaperSection = function (qType, direction) {
        const store = window.PaperStore;
        if (store.meta.paper_type === 'exam_19' || !['up', 'down'].includes(direction)) return;
        const present = store.cart.map(item => store.questionsMap[item.id])
            .filter(q => q && q.content && q.content.trim())
            .map(q => q.question_type || 'single_choice');
        const order = getPaperTypeOrder(present, store.meta.paper_type, store.meta.section_order);
        const visible = order.filter(type => present.includes(type));
        const index = visible.indexOf(qType);
        const targetIndex = index + (direction === 'up' ? -1 : 1);
        if (index < 0 || targetIndex < 0 || targetIndex >= visible.length) return;
        const from = order.indexOf(qType);
        order.splice(from, 1);
        const to = order.indexOf(visible[targetIndex]);
        order.splice(direction === 'up' ? to : to + 1, 0, qType);
        store.meta.section_order = order;
        saveMetaToStorage();
        window.renderPaperCanvas();
        const focusSection = () => {
            const section = Array.from(document.querySelectorAll('.paper-sec-block'))
                .find(element => element.dataset.qtype === qType);
            if (!section) return;
            section.scrollIntoView({ block: 'nearest' });
            const buttons = Array.from(section.querySelectorAll('[data-section-move]'));
            const button = buttons.find(item => item.dataset.sectionMove === direction && !item.disabled)
                || buttons.find(item => !item.disabled);
            if (button) button.focus({ preventScroll: true });
        };
        // Follow the moved heading after the canvas has restored its scroll position.
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(focusSection);
        else focusSection();
    };

    // Reorder Items strictly within the same Question Type section
    function reorderItemsWithinType(cart, qType, fromSubIdx, toSubIdx) {
        const itemsOfType = [];
        cart.forEach((item) => {
            const q = window.PaperStore.questionsMap[item.id];
            const t = q ? q.question_type : 'single_choice';
            if (t === qType) {
                itemsOfType.push(item);
            }
        });

        if (fromSubIdx < 0 || fromSubIdx >= itemsOfType.length || toSubIdx < 0 || toSubIdx >= itemsOfType.length) {
            return cart;
        }

        const moved = itemsOfType.splice(fromSubIdx, 1)[0];
        itemsOfType.splice(toSubIdx, 0, moved);

        const newCart = [...cart];
        let subIdx = 0;
        cart.forEach((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id];
            const t = q ? q.question_type : 'single_choice';
            if (t === qType) {
                newCart[idx] = itemsOfType[subIdx];
                subIdx++;
            }
        });

        return newCart;
    }

    // Move Question Order within same question type
    window.movePaperQuestionWithinType = function (qType, subIndex, direction) {
        const cart = window.PaperStore.cart;
        const targetSubIdx = direction === 'up' ? subIndex - 1 : subIndex + 1;
        window.PaperStore.cart = reorderItemsWithinType(cart, qType, subIndex, targetSubIdx);
        saveCartToStorage();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    // Real-Time Dynamic Drag and Drop for A4 Paper Canvas Items (Restricted to same question type)
    let draggedItemData = null;
    let dragPlaceholder = null;

    function resetPaperDragTarget() {
        if (!draggedItemData) return;
        draggedItemData.target = null;
        const source = draggedItemData.element;
        if (dragPlaceholder && source.isConnected && source.parentNode) {
            source.parentNode.insertBefore(dragPlaceholder, source);
        }
    }

    function getPaperDragTargetPosition(cart, questionsMap, qType, sourceId, targetId, placeAfter) {
        const items = cart.filter(item => {
            const question = questionsMap[item.id];
            return question && question.question_type === qType;
        });
        const fromIndex = items.findIndex(item => item.id === sourceId);
        const targetIndex = items.findIndex(item => item.id === targetId);
        if (fromIndex < 0 || targetIndex < 0 || sourceId === targetId) return null;
        const insertionIndex = targetIndex + (placeAfter ? 1 : 0);
        return { fromIndex, toIndex: insertionIndex - (fromIndex < insertionIndex ? 1 : 0) };
    }

    window.onPaperCanvasDragStart = function (e, qid, subIndex, qType) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (!card) return;

        draggedItemData = { 
            qid: parseInt(qid, 10),
            qType: qType,
            element: card,
            target: null
        };

        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(qid));

        // Create or reuse dynamic drop placeholder
        if (!dragPlaceholder) {
            dragPlaceholder = document.createElement('div');
            dragPlaceholder.className = 'paper-drag-placeholder border-2 border-dashed border-brand-500 bg-brand-50/70 rounded-xl my-2 flex items-center justify-center text-xs font-semibold text-brand-600 shadow-inner transition-all duration-200 select-none';
            dragPlaceholder.style.height = `${Math.max(48, card.offsetHeight - 8)}px`;
            dragPlaceholder.innerHTML = '<span class="flex items-center space-x-1.5"><i class="fa-solid fa-arrow-down-long text-brand-500 animate-bounce"></i> <span>释放在同题型内插入试题</span></span>';
            // The placeholder is a sibling of the card, so its drop does not
            // bubble through the card's inline handlers.
            dragPlaceholder.addEventListener('dragover', event => {
                if (draggedItemData && draggedItemData.target) {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'move';
                }
            });
            dragPlaceholder.addEventListener('drop', event => window.onPaperCanvasDrop(event));
        }

        // Apply drag style to current card after browser creates drag ghost image
        setTimeout(() => {
            if (card.isConnected && draggedItemData && draggedItemData.element === card) {
                card.classList.add('opacity-30', 'scale-[0.98]', 'bg-slate-100');
                if (card.parentNode) {
                    card.parentNode.insertBefore(dragPlaceholder, card);
                }
            }
        }, 0);
    };

    window.onPaperCanvasDragOver = function (e) {
        e.preventDefault();
        if (!draggedItemData || !dragPlaceholder) return;

        const targetCard = e.target.closest('.paper-q-item');
        if (!targetCard || targetCard === draggedItemData.element) {
            if (!dragPlaceholder.contains(e.target)) resetPaperDragTarget();
            return;
        }

        // Strict boundary: check if targetCard belongs to the SAME question type section!
        const targetQType = targetCard.dataset.qtype;
        if (targetQType !== draggedItemData.qType) {
            // Different question type section! Disallow drag placeholder insertion
            e.dataTransfer.dropEffect = 'none';
            resetPaperDragTarget();
            return;
        }

        e.dataTransfer.dropEffect = 'move';
        const rect = targetCard.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;
        draggedItemData.target = {
            qid: parseInt(targetCard.dataset.qid, 10),
            placeAfter: e.clientY >= midY
        };

        if (e.clientY < midY) {
            if (targetCard.previousElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard);
            }
        } else {
            if (targetCard.nextElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard.nextElementSibling);
            }
        }
    };

    window.onPaperCanvasDragEnter = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragLeave = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragEnd = function (e, didDrop = false) {
        const drag = draggedItemData;
        if (!drag) return;
        const card = e.currentTarget.closest('.paper-q-item');
        if (card) {
            card.classList.remove('opacity-30', 'scale-[0.98]', 'bg-slate-100');
        }

        if (dragPlaceholder && dragPlaceholder.parentNode) {
            dragPlaceholder.parentNode.removeChild(dragPlaceholder);
        }
        draggedItemData = null;
        dragPlaceholder = null;
        // Each question has its own page-block wrapper. Resolve the insertion
        // against the cart's same-type IDs, independently of wrappers or pages.
        const position = didDrop && drag.target ? getPaperDragTargetPosition(
            window.PaperStore.cart, window.PaperStore.questionsMap,
            drag.qType, drag.qid, drag.target.qid, drag.target.placeAfter
        ) : null;
        const reordered = position && position.fromIndex !== position.toIndex;
        if (reordered) {
            window.PaperStore.cart = reorderItemsWithinType(
                window.PaperStore.cart, drag.qType, position.fromIndex, position.toIndex
            );
            saveCartToStorage();
        }
        renderPart3QuestionStream();
        window.renderPaperCanvas();
        if (reordered && window.showToast) window.showToast('试题顺序已更新', 'info');
    };

    window.onPaperCanvasDrop = function (e) {
        e.preventDefault();
        const drag = draggedItemData;
        const targetCard = e.target.closest('.paper-q-item');
        const onPlaceholder = dragPlaceholder && dragPlaceholder.contains(e.target);
        // A final drop can follow the last dragover at a different position.
        // Never commit a stale target when released on the source or elsewhere.
        const validDrop = Boolean(drag && drag.target && (onPlaceholder || (
            targetCard && targetCard !== drag.element
            && targetCard.dataset.qtype === drag.qType
            && parseInt(targetCard.dataset.qid, 10) === drag.target.qid
        )));
        window.onPaperCanvasDragEnd(e, validDrop);
    };

    // Solution Space Handlers
    function restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta) {
        if (!Number.isFinite(anchorTop)) return;

        const restoreAnchor = () => {
            const nextControl = document.querySelector(`[data-solution-space-controls-qid="${qid}"]`);
            if (!nextControl) return;
            const shift = nextControl.getBoundingClientRect().top - anchorTop;
            if (Math.abs(shift) > 0.5) {
                let scrollParent = nextControl.parentElement;
                while (scrollParent) {
                    const style = window.getComputedStyle(scrollParent);
                    const canScroll = /(auto|scroll)/.test(style.overflowY || '') &&
                        scrollParent.scrollHeight > scrollParent.clientHeight + 1;
                    if (canScroll) {
                        scrollParent.scrollTop += shift;
                        break;
                    }
                    scrollParent = scrollParent.parentElement;
                }
            }
            if (activeDelta !== null && activeDelta !== undefined) {
                const nextButton = Array.from(
                    nextControl.querySelectorAll('[data-solution-space-delta]')
                ).find(button => button.getAttribute('data-solution-space-delta') === String(activeDelta));
                if (nextButton) nextButton.focus({ preventScroll: true });
            }
        };

        // renderPaperCanvas schedules its own scroll restoration. Queue this
        // anchor correction afterwards, then repeat once for late layout.
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(() => {
                restoreAnchor();
                requestAnimationFrame(restoreAnchor);
            });
        } else {
            restoreAnchor();
        }
    }

    window.updateQuestionSolutionSpace = function (qid, delta) {
        qid = parseInt(qid, 10);
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (!item) return;
        const currentControl = document.querySelector(
            `[data-solution-space-controls-qid="${qid}"]`
        );
        const anchorTop = currentControl
            ? currentControl.getBoundingClientRect().top
            : Number.NaN;
        const activeDelta = currentControl && currentControl.contains(document.activeElement)
            ? document.activeElement.getAttribute('data-solution-space-delta')
            : null;
        const defaultSpace = parseFloat(window.PaperStore.meta.solution_space_default || '7.0');
        let currentSpace = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
        if (isNaN(currentSpace)) currentSpace = 7.0;
        
        let newSpace = Math.max(0.0, Math.min(15.0, Math.round((currentSpace + delta) * 10) / 10));
        item.solution_space = newSpace.toFixed(1);
        saveCartToStorage();
        window.renderPaperCanvas();
        restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta);
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`题目 #${seqNum} 留白高度设为 ${newSpace.toFixed(1)} cm`, 'info');
    };

    window.updateGlobalSolutionSpace = function (val) {
        const spaceVal = parseFloat(val).toFixed(1);
        window.PaperStore.meta.solution_space_default = spaceVal;
        window.PaperStore.cart.forEach(item => {
            item.solution_space = spaceVal;
        });
        saveMetaToStorage();
        saveCartToStorage();
        window.renderPaperCanvas();
        if (window.showToast) {
            window.showToast(`解答题全局留白设为 ${spaceVal} cm（点击 PDF 预览可即时生效）`, 'success');
        }
    };

    // Helper: build cart questions payload with solution_space
    function buildCartQuestionsPayload() {
        const cart = window.PaperStore.cart;
        const missingIds = getMissingCartQuestionIds();
        if (missingIds.length > 0) {
            throw new Error(`Cannot build paper payload with ${missingIds.length} unloaded question(s)`);
        }
        const defaultSpace = (window.PaperStore.meta.solution_space_default || '7.0').toString();
        return cart.map((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id];
            return {
                id: item.id,
                score: item.score,
                order: idx + 1,
                figure_align: getQuestionFigAlign(q),
                figure_align_custom: Boolean(q.figure_align_custom || q.custom_figure_align),
                figure_size: getQuestionFigSize(q),
                solution_space: item.solution_space !== undefined ? item.solution_space.toString() : defaultSpace
            };
        });
    }

    // Export PDF, Word, TeX, and Save Handlers
    window.exportPaperPdf = async function (target = 'paper') {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 PDF', 'warning');
            return;
        }

        const targetName = (target === 'sheet') ? '答题卡' : '试卷';
        const iconEmoji = (target === 'sheet') ? '📝' : '📄';
        const actionKey = `pdf:${target}`;
        if (!beginPaperAction(actionKey, `导出${targetName} PDF`)) return;
        const expectedCartSignature = getPaperCartSignature();

        // Pre-open single tab synchronously during click event -> 100% bypasses popup blockers!
        const tab = window.open('', '_blank');
        setPdfTabLoadingState(tab, `${iconEmoji} ${targetName} PDF 编译中`, iconEmoji, `正在为您在线静默编译 ${targetName} 高清 PDF...`);

        try {
            if (!await ensurePaperCartReady(`导出${targetName} PDF`, expectedCartSignature)) {
                if (tab && !tab.closed) {
                    setPdfTabErrorState(tab, `${targetName} PDF 暂不可导出`, null, '已选题目尚未完整加载，请返回工作台重新加载。');
                }
                return;
            }
            if (window.showToast) {
                window.showToast(`正在静默编译 ${targetName} PDF...`, 'info');
            }

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                section_order: normalizeSectionOrder(window.PaperStore.meta.section_order),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                target: target,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok && res.headers.get('content-type')?.includes('application/pdf')) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                if (tab && !tab.closed) {
                    tab.location.href = url;
                }
                if (window.showToast) window.showToast(`${targetName} PDF 编译成功！已在新窗口打开`, 'success');
            } else {
                let errLog = `${targetName} PDF 编译失败`;
                let errData = {};
                try {
                    errData = await res.json();
                    if (errData.message) errLog = errData.message;
                } catch (e) {}
                if (tab && !tab.closed) {
                    setPdfTabErrorState(tab, `${targetName} PDF 编译失败`, errData.diagnostic, errLog);
                }
                if (window.showToast) window.showToast(errLog, 'error');
            }
        } catch (e) {
            if (tab && !tab.closed) tab.close();
            if (window.showToast) window.showToast('PDF 请求编译异常', 'error');
        } finally {
            finishPaperAction(actionKey);
        }
    };

    function setPdfTabLoadingState(tab, title, iconEmoji, text) {
        if (!tab) return;
        try {
            const safeTitle = escapeHtml(title);
            const safeIcon = escapeHtml(iconEmoji);
            const safeText = escapeHtml(text);
            tab.document.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>${safeTitle}</title>
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                            background-color: #0f172a;
                            color: #f8fafc;
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                            display: flex;
                            min-height: 100vh;
                            align-items: center;
                            justify-content: center;
                        }
                        .card {
                            background: #1e293b;
                            border: 1px solid #334155;
                            border-radius: 16px;
                            padding: 32px 40px;
                            text-align: center;
                            box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
                            max-width: 360px;
                        }
                        .icon-box {
                            width: 56px;
                            height: 56px;
                            border-radius: 14px;
                            background: rgba(99, 102, 241, 0.15);
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-size: 28px;
                            margin: 0 auto 16px auto;
                        }
                        h2 { margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #ffffff; }
                        p { margin: 0 0 20px 0; font-size: 13px; color: #94a3b8; line-height: 1.5; }
                        .status {
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            gap: 8px;
                            color: #818cf8;
                            font-size: 13px;
                            font-weight: 600;
                        }
                        @keyframes spin { 100% { transform: rotate(360deg); } }
                        .spinner {
                            width: 14px;
                            height: 14px;
                            border: 2px solid rgba(99, 102, 241, 0.3);
                            border-top-color: #818cf8;
                            border-radius: 50%;
                            animation: spin 0.8s linear infinite;
                        }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="icon-box">${safeIcon}</div>
                        <h2>${safeTitle}</h2>
                        <p>${safeText}</p>
                        <div class="status">
                            <div class="spinner"></div>
                            <span>LaTeX 引擎静默编译中...</span>
                        </div>
                    </div>
                </body>
                </html>
            `);
            tab.document.close();
        } catch(e) {}
    }

    function setPdfTabErrorState(tab, title, diagnostic, fallbackMessage) {
        if (!tab) return;
        const report = diagnostic || {};
        const fixes = Array.isArray(report.fixes) && report.fixes.length
            ? report.fixes
            : ['返回题目编辑页，核对报错位置附近的公式或排版命令。'];
        const fixesHtml = fixes.map(item => `<li>${escapeHtml(item)}</li>`).join('');
        const sourceHtml = report.source_context
            ? `<details><summary>查看出错位置附近的 LaTeX 源码</summary><pre>${escapeHtml(report.source_context)}</pre></details>`
            : '';
        const technicalHtml = report.technical_error
            ? `<details><summary>查看编译器技术信息</summary><pre>${escapeHtml(report.technical_error)}</pre></details>`
            : '';
        try {
            tab.document.open();
            tab.document.write(`
                <!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                    <meta charset="utf-8">
                    <title>${escapeHtml(title)}</title>
                    <style>
                        * { box-sizing: border-box; }
                        body { margin: 0; padding: 36px 20px; background: #f8fafc; color: #1e293b; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }
                        .card { max-width: 760px; margin: 0 auto; background: white; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px; box-shadow: 0 18px 45px rgba(15,23,42,.08); }
                        .head { display: flex; gap: 14px; align-items: flex-start; }
                        .icon { width: 46px; height: 46px; flex: 0 0 46px; border-radius: 13px; background: #fff1f2; color: #e11d48; display: grid; place-items: center; font-size: 23px; }
                        h1 { margin: 0 0 6px; font-size: 20px; }
                        .badge { display: inline-block; margin-top: 4px; padding: 3px 8px; border-radius: 999px; background: ${report.ai_used ? '#eef2ff' : '#f1f5f9'}; color: ${report.ai_used ? '#4f46e5' : '#64748b'}; font-size: 12px; }
                        .section { margin-top: 22px; padding-top: 18px; border-top: 1px solid #e2e8f0; }
                        h2 { margin: 0 0 8px; font-size: 14px; color: #475569; }
                        p, li { font-size: 14px; line-height: 1.75; }
                        p { margin: 0; }
                        ol { margin: 6px 0 0; padding-left: 22px; }
                        code { background: #f1f5f9; border-radius: 5px; padding: 2px 5px; }
                        details { margin-top: 14px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }
                        summary { cursor: pointer; color: #475569; font-size: 13px; font-weight: 600; }
                        pre { margin: 10px 0 0; padding: 12px; border-radius: 8px; overflow: auto; background: #0f172a; color: #e2e8f0; font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
                        button { margin-top: 22px; border: 0; border-radius: 9px; padding: 10px 16px; background: #334155; color: white; cursor: pointer; }
                    </style>
                </head>
                <body>
                    <main class="card">
                        <div class="head">
                            <div class="icon">!</div>
                            <div>
                                <h1>${escapeHtml(report.summary || fallbackMessage || title)}</h1>
                                <p>${escapeHtml(report.location || '试卷公式或模板附近')}</p>
                                <span class="badge">${report.ai_used ? 'AI 已结合编译日志解释' : '本地诊断结果'}</span>
                            </div>
                        </div>
                        <section class="section"><h2>为什么会这样</h2><p>${escapeHtml(report.cause || fallbackMessage || 'LaTeX 编译没有完成。')}</p></section>
                        <section class="section"><h2>建议如何修复</h2><ol>${fixesHtml}</ol></section>
                        ${sourceHtml}
                        ${technicalHtml}
                        <button onclick="window.close()">关闭此页并返回修改</button>
                    </main>
                </body>
                </html>
            `);
            tab.document.close();
        } catch (e) {}
    }

    function setPandocModalVisible(visible) {
        const modal = document.getElementById('pandocInstallModal');
        if (!modal) return;
        const surface = modal.querySelector('[data-modal-surface]');
        if (visible) {
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            requestAnimationFrame(() => {
                modal.classList.remove('opacity-0');
                if (surface) surface.classList.remove('scale-95');
            });
            modal.setAttribute('aria-hidden', 'false');
        } else {
            modal.classList.add('opacity-0');
            if (surface) surface.classList.add('scale-95');
            modal.setAttribute('aria-hidden', 'true');
            setTimeout(() => {
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }, 200);
        }
    }

    function resetPandocInstallModal() {
        const progress = document.getElementById('pandocInstallProgress');
        const error = document.getElementById('pandocInstallError');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        if (progress) progress.classList.add('hidden');
        if (error) {
            error.classList.add('hidden');
            error.textContent = '';
        }
        if (install) {
            install.disabled = false;
            install.innerHTML = '<i class="fa-solid fa-download mr-1.5" aria-hidden="true"></i>安装并继续导出';
        }
        if (compatibility) compatibility.disabled = false;
        if (cancel) cancel.disabled = false;
    }

    function updatePandocInstallProgress(state) {
        const progress = document.getElementById('pandocInstallProgress');
        const text = document.getElementById('pandocInstallProgressText');
        const value = document.getElementById('pandocInstallProgressValue');
        const bar = document.getElementById('pandocInstallProgressBar');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        const percent = Math.max(0, Math.min(100, parseInt(state.progress || '0', 10)));
        if (progress) progress.classList.remove('hidden');
        if (text) text.textContent = state.message || '正在准备 Word 可编辑公式组件…';
        if (value) value.textContent = `${percent}%`;
        if (bar) bar.style.width = `${percent}%`;
        if (install) {
            install.disabled = true;
            install.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1.5" aria-hidden="true"></i>正在安装…';
        }
        if (compatibility) compatibility.disabled = true;
        if (cancel) cancel.disabled = true;
    }

    function showPandocInstallError(message) {
        const error = document.getElementById('pandocInstallError');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        if (error) {
            error.textContent = message || 'Word 可编辑公式组件安装失败，可重试或选择本次兼容导出。';
            error.classList.remove('hidden');
        }
        if (install) {
            install.disabled = false;
            install.innerHTML = '<i class="fa-solid fa-rotate-right mr-1.5" aria-hidden="true"></i>重新安装';
        }
        if (compatibility) compatibility.disabled = false;
        if (cancel) cancel.disabled = false;
    }

    function waitForPandocDecision() {
        resetPandocInstallModal();
        setPandocModalVisible(true);
        return new Promise(resolve => {
            const install = document.getElementById('pandocInstallBtn');
            const compatibility = document.getElementById('pandocCompatibilityBtn');
            const cancel = document.getElementById('pandocCancelBtn');
            const finish = decision => {
                if (install) install.onclick = null;
                if (compatibility) compatibility.onclick = null;
                if (cancel) cancel.onclick = null;
                if (decision !== 'install') setPandocModalVisible(false);
                resolve(decision);
            };
            if (install) install.onclick = () => finish('install');
            if (compatibility) compatibility.onclick = () => finish('compatibility');
            if (cancel) cancel.onclick = () => finish('cancel');
        });
    }

    async function pollPandocInstall(taskId) {
        const deadline = Date.now() + 15 * 60 * 1000;
        while (Date.now() < deadline) {
            const response = await fetch(`/api/runtime/pandoc/install/${encodeURIComponent(taskId)}`);
            if (!response.ok) throw new Error('Pandoc 安装任务已失效。');
            const data = await response.json();
            const state = data.pandoc || {};
            updatePandocInstallProgress(state);
            if (state.status === 'completed' || state.status === 'ready') return true;
            if (state.status === 'error') throw new Error(state.error || state.message || 'Pandoc 安装失败。');
            await new Promise(resolve => setTimeout(resolve, 750));
        }
        throw new Error('Pandoc 安装等待超时，请重试。');
    }

    async function installPandocAndContinue(existingTaskId = null) {
        while (true) {
            try {
                let state;
                if (existingTaskId) {
                    state = { task_id: existingTaskId };
                    existingTaskId = null;
                } else {
                    updatePandocInstallProgress({ progress: 0, message: '正在准备安装…' });
                    const response = await fetch('/api/runtime/pandoc/install', { method: 'POST' });
                    const data = await response.json();
                    if (!response.ok || data.status !== 'success') {
                        throw new Error(data.message || 'Pandoc 安装任务创建失败。');
                    }
                    state = data.pandoc || {};
                }
                if (state.status !== 'ready') {
                    if (!state.task_id) throw new Error('Pandoc 安装任务缺少标识。');
                    await pollPandocInstall(state.task_id);
                }
                setPandocModalVisible(false);
                if (window.showToast) window.showToast('Word 可编辑公式组件已安装，正在继续导出…', 'success');
                return true;
            } catch (error) {
                showPandocInstallError(error && error.message ? error.message : 'Pandoc 安装失败。');
                const decision = await new Promise(resolve => {
                    const install = document.getElementById('pandocInstallBtn');
                    const compatibility = document.getElementById('pandocCompatibilityBtn');
                    const cancel = document.getElementById('pandocCancelBtn');
                    if (install) install.onclick = () => resolve('retry');
                    if (compatibility) compatibility.onclick = () => resolve('compatibility');
                    if (cancel) cancel.onclick = () => resolve('cancel');
                });
                if (decision === 'retry') continue;
                setPandocModalVisible(false);
                return decision === 'compatibility';
            }
        }
    }

    async function ensurePandocForWordExport() {
        let response;
        try {
            response = await fetch('/api/runtime/pandoc/status');
            if (!response.ok) throw new Error('无法检查 Pandoc 状态。');
            const data = await response.json();
            const state = data.pandoc || {};
            if (state.available || state.status === 'ready') return true;
            if (['queued', 'downloading', 'verifying'].includes(state.status) && state.task_id) {
                resetPandocInstallModal();
                setPandocModalVisible(true);
                updatePandocInstallProgress(state);
                return await installPandocAndContinue(state.task_id);
            }
            if (state.status === 'unsupported') {
                if (window.showToast) window.showToast('当前系统暂不支持自动安装 Pandoc，可使用兼容模式导出', 'warning');
            }
        } catch (error) {
            if (window.showToast) window.showToast(error.message || '无法检查 Word 公式组件', 'error');
            return false;
        }

        const decision = await waitForPandocDecision();
        if (decision === 'cancel') return false;
        if (decision === 'compatibility') return true;
        return installPandocAndContinue();
    }

    async function generateAndDownloadWord(payload) {
        try {
            const isExam19 = payload.paper_type === 'exam_19';
            if (window.showToast) {
                window.showToast(isExam19 ? '正在生成 Word 试卷及解析压缩包（正文+解析，不含答题卡）...' : '正在生成 Word 试卷及参考答案压缩包...', 'info');
            }
            const res = await fetch('/api/paper/export/word', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                let message = 'Word 导出失败';
                try {
                    const data = await res.json();
                    message = data.message || message;
                } catch (e) {}
                if (window.showToast) window.showToast(message, 'error');
                return;
            }

            const blob = await res.blob();
            const rawTitle = (payload.title || '试卷').trim();
            const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
            const filename = `${safeTitle}_Word打包.zip`;
            const nativeCount = parseInt(res.headers.get('X-Word-Native-Formulas') || '0', 10);
            const fallbackCount = parseInt(res.headers.get('X-Word-Fallback-Formulas') || '0', 10);
            const failedCount = parseInt(res.headers.get('X-Word-Failed-Formulas') || '0', 10);

            if (typeof window.showSaveFilePicker === 'function') {
                try {
                    const handle = await window.showSaveFilePicker({
                        suggestedName: filename,
                        types: [{
                            description: 'Zip Archive',
                            accept: { 'application/zip': ['.zip'] }
                        }]
                    });
                    const writable = await handle.createWritable();
                    await writable.write(blob);
                    await writable.close();
                } catch (err) {
                    if (err && err.name === 'AbortError') return;
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = filename;
                    anchor.click();
                    URL.revokeObjectURL(url);
                }
            } else {
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = filename;
                anchor.click();
                URL.revokeObjectURL(url);
            }

            if (window.showToast) {
                const baseTip = `Word 打包《${filename}》导出成功（含试卷正文与含答案解析两份文档）！`;
                if (failedCount > 0) {
                    window.showToast(`${baseTip}，有 ${failedCount} 处公式已用红字标出`, 'warning');
                } else if (fallbackCount > 0) {
                    window.showToast(`${baseTip}，${nativeCount} 处原生公式，${fallbackCount} 处图片保真公式`, 'warning');
                } else {
                    window.showToast(`${baseTip}，${nativeCount} 处公式均可直接编辑`, 'success');
                }
            }
        } catch (e) {
            if (window.showToast) window.showToast('Word 生成请求异常', 'error');
        }
    }

    window.exportPaperWord = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 Word 试卷', 'warning');
            return;
        }
        const actionKey = 'word';
        if (!beginPaperAction(actionKey, '导出 Word 试卷')) return;
        const expectedCartSignature = getPaperCartSignature();
        try {
            if (!await ensurePaperCartReady('导出 Word 试卷', expectedCartSignature)) return;
            if (!await ensurePandocForWordExport()) return;
            if (!isPaperCartSnapshotCurrent(expectedCartSignature, '导出 Word 试卷')) return;
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                section_order: normalizeSectionOrder(window.PaperStore.meta.section_order),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: buildCartQuestionsPayload()
            };
            await generateAndDownloadWord(payload);
        } finally {
            finishPaperAction(actionKey);
        }
    };

    window.exportPaperBundle = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法打包导出 LaTeX 资源包', 'warning');
            return;
        }
        const actionKey = 'bundle';
        if (!beginPaperAction(actionKey, '打包导出 LaTeX 资源')) return;
        const expectedCartSignature = getPaperCartSignature();

        try {
            if (!await ensurePaperCartReady('打包导出 LaTeX 资源', expectedCartSignature)) return;
            if (window.showToast) window.showToast('正在打包 LaTeX 源码并编译全套 PDF 归档...', 'info');

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                section_order: normalizeSectionOrder(window.PaperStore.meta.section_order),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/bundle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                const blob = await res.blob();
                const rawTitle = (window.PaperStore.meta.title || '试卷').trim();
                const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
                const filename = `${safeTitle}_LaTeX打包.zip`;

                if (typeof window.showSaveFilePicker === 'function') {
                    try {
                        const handle = await window.showSaveFilePicker({
                            suggestedName: filename,
                            types: [{
                                description: 'Zip Archive',
                                accept: { 'application/zip': ['.zip'] }
                            }]
                        });
                        const writable = await handle.createWritable();
                        await writable.write(blob);
                        await writable.close();
                        if (window.showToast) window.showToast(`LaTeX 打包归档已保存至指定目录`, 'success');
                        return;
                    } catch (err) {
                        if (err && err.name === 'AbortError') return;
                    }
                }

                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(url);
                if (window.showToast) window.showToast(`LaTeX 打包《${filename}》导出成功！`, 'success');
            } else {
                const errData = await res.json();
                if (window.showToast) window.showToast(errData.message || '导出失败', 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast('LaTeX 打包请求异常', 'error');
        } finally {
            finishPaperAction(actionKey);
        }
    };

    window.savePaperToDb = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法保存试卷', 'warning');
            return;
        }
        const actionKey = 'save';
        if (!beginPaperAction(actionKey, '保存试卷')) return;
        const expectedCartSignature = getPaperCartSignature();

        try {
            if (!await ensurePaperCartReady('保存试卷', expectedCartSignature)) return;
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                section_order: normalizeSectionOrder(window.PaperStore.meta.section_order),
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cart
            };

            const res = await fetch('/api/paper/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('试卷已保存到数据库，题目引用次数已自动更新！', 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '保存失败', 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast('保存试卷请求异常', 'error');
        } finally {
            finishPaperAction(actionKey);
        }
    };

    // ----------------- Saved Papers Archive Library -----------------
    async function renderSavedPapersWorkspace() {
        const container = document.getElementById('savedPapersListContainer');
        const countEl = document.getElementById('savedPaperTotalCount');
        if (!container) return;

        container.innerHTML = `
            <div class="records-loading-state ui-state ui-state-loading" role="status" aria-live="polite">
                <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-spinner fa-spin"></i></span>
                <strong class="ui-state-title">正在获取历史试卷</strong>
                <span class="ui-state-description">已保存的试卷记录加载完成后会显示在这里。</span>
            </div>
        `;

        try {
            const res = await fetch('/api/papers');
            const data = await res.json();
            if (data.status !== 'success' || !Array.isArray(data.data)) {
                throw new Error(data.message || '无法读取历史试卷');
            }

            const papers = data.data;
            if (countEl) countEl.textContent = String(papers.length);
            if (papers.length === 0) {
                container.innerHTML = `
                    <div class="records-empty-state ui-state ui-state-empty">
                        <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-box-open"></i></span>
                        <strong class="ui-state-title">暂无保存的历史试卷</strong>
                        <span class="ui-state-description">在智能组卷工作区完成编排后，点击“保存试卷”即可归档到这里。</span>
                    </div>
                `;
                return;
            }

            const paperTypeMap = {
                exam_19: '19题高考卷',
                exam: '常规试卷',
                quiz: '日常小练',
                handout: '讲义/教案'
            };
            container.innerHTML = `<div class="records-paper-grid">${papers.map((paper) => {
                const paperId = Number.parseInt(paper.id, 10);
                const typeLabel = paperTypeMap[paper.paper_type] || '试卷';
                const dateText = paper.created_at
                    ? new Date(paper.created_at).toLocaleString('zh-CN', {
                        year: 'numeric', month: '2-digit', day: '2-digit',
                        hour: '2-digit', minute: '2-digit'
                    })
                    : '未知时间';
                const score = escapeHtml(String(paper.total_score ?? 0));
                const questionCount = escapeHtml(String(paper.question_count ?? 0));
                const title = escapeHtml(paper.title || '未命名试卷');
                const subtitle = paper.subtitle
                    ? `备注：${escapeHtml(paper.subtitle)}`
                    : '暂无备注';

                return `
                    <article class="saved-paper-card">
                        <div class="saved-paper-card-heading">
                            <span class="saved-paper-type-badge">${escapeHtml(typeLabel)}</span>
                            <h4 title="${title}">${title}</h4>
                        </div>
                        <div class="saved-paper-card-meta">
                            <span><i class="fa-solid fa-calculator" aria-hidden="true"></i> ${score} 分</span>
                            <span><i class="fa-solid fa-list-check" aria-hidden="true"></i> ${questionCount} 题</span>
                            <span><i class="fa-regular fa-clock" aria-hidden="true"></i> ${escapeHtml(dateText)}</span>
                        </div>
                        <p class="saved-paper-card-note">${subtitle}</p>
                        <div class="saved-paper-card-actions">
                            <button type="button" class="saved-paper-load-action" onclick="loadSavedPaper(${paperId})" title="载入试卷至智能组卷工作区">
                                <i class="fa-solid fa-arrow-right-to-bracket" aria-hidden="true"></i><span>载入试卷</span>
                            </button>
                            <button type="button" class="saved-paper-pdf-action" onclick="quickExportPaperPdf(${paperId})" title="快速编译 PDF">
                                <i class="fa-solid fa-file-pdf" aria-hidden="true"></i><span>导出 PDF</span>
                            </button>
                            <button type="button" class="saved-paper-delete-action" onclick="deleteSavedPaper(${paperId})" aria-label="删除试卷 ${title}" title="删除此保存试卷">
                                <i class="fa-solid fa-trash-can" aria-hidden="true"></i>
                            </button>
                        </div>
                    </article>
                `;
            }).join('')}</div>`;
        } catch (error) {
            console.error(error);
            if (countEl) countEl.textContent = '0';
            container.innerHTML = `
                <div class="records-error-state ui-state ui-state-error" role="alert">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-circle-exclamation"></i></span>
                    <strong class="ui-state-title">历史试卷加载失败</strong>
                    <span class="ui-state-description">${escapeHtml(error.message || '请稍后重试')}</span>
                </div>
            `;
        }
    }

    window.closeSavedPapersModal = function () {
        const modal = document.getElementById('savedPapersModal');
        if (!modal) {
            if (window.PaperStore.activeWorkspace === 'records' && typeof window.selectWorkspace === 'function') {
                window.selectWorkspace('paper', '智能组卷');
            }
            return;
        }
        window.MathBankModal.close(modal);
        modal.remove();
    };

    window.openSavedPapersModal = async function () {
        const recordsWorkspace = document.getElementById('recordsWorkspaceSection');
        if (recordsWorkspace && typeof window.selectWorkspace === 'function') {
            window.selectWorkspace('records', '试卷记录');
            await renderSavedPapersWorkspace();
            return;
        }

        let modal = document.getElementById('savedPapersModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }

        modal = document.createElement('div');
        modal.id = 'savedPapersModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'savedPapersModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <!-- Header -->
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-amber-500/10 text-amber-600 dark:text-amber-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-folder-open"></i>
                        </div>
                        <div>
                            <h3 id="savedPapersModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">历史试卷归档库</h3>
                            <p class="text-xs text-slate-400">查看、一键载入还原或删除已保存的历史试卷记录</p>
                        </div>
                    </div>
                    <button type="button" data-modal-close onclick="closeSavedPapersModal()" aria-label="关闭历史试卷归档库" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>

                <!-- Body (Scrollable List) -->
                <div class="p-6 overflow-y-auto flex-1 space-y-3" id="savedPapersListContainer">
                    <div class="text-center py-12 text-slate-400 font-sans text-xs">
                        <i class="fa-solid fa-spinner fa-spin text-xl text-brand-500 mb-2 block"></i>
                        正在获取历史试卷列表...
                    </div>
                </div>

                <!-- Footer -->
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-between text-xs text-slate-400">
                    <span>共保存 <strong id="savedPaperTotalCount" class="text-slate-700 dark:text-slate-200">0</strong> 份历史试卷</span>
                    <button type="button" onclick="closeSavedPapersModal()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium transition-colors dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">
                        关闭窗口
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeSavedPapersModal });

        try {
            const res = await fetch('/api/papers');
            const data = await res.json();
            const container = document.getElementById('savedPapersListContainer');
            const countEl = document.getElementById('savedPaperTotalCount');

            if (data.status === 'success' && data.data) {
                const papers = data.data;
                if (countEl) countEl.textContent = papers.length;

                if (papers.length === 0) {
                    container.innerHTML = `
                        <div class="text-center py-16">
                            <div class="w-14 h-14 mx-auto rounded-3xl bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600 flex items-center justify-center text-2xl mb-3">
                                <i class="fa-solid fa-box-open"></i>
                            </div>
                            <p class="text-sm font-semibold text-slate-500 dark:text-slate-400">暂无保存的历史试卷</p>
                            <p class="text-xs text-slate-400 mt-1">在组卷工作台中挑选题目后点击“保存试卷”即可归档在此处</p>
                        </div>
                    `;
                    return;
                }

                const paperTypeMap = {
                    'exam_19': { label: '19题高考卷', color: 'bg-indigo-50 text-indigo-600 border-indigo-200 dark:bg-indigo-950/40 dark:border-indigo-800 dark:text-indigo-300' },
                    'exam': { label: '常规试卷', color: 'bg-emerald-50 text-emerald-600 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300' },
                    'quiz': { label: '日常小练', color: 'bg-amber-50 text-amber-600 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300' },
                    'handout': { label: '讲义/教案', color: 'bg-rose-50 text-rose-600 border-rose-200 dark:bg-rose-950/40 dark:border-rose-800 dark:text-rose-300' }
                };

                container.innerHTML = papers.map(p => {
                    const typeInfo = paperTypeMap[p.paper_type] || { label: '试卷', color: 'bg-slate-100 text-slate-600' };
                    const dateStr = p.created_at ? new Date(p.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未知时间';

                    return `
                        <div class="bg-slate-50/70 dark:bg-slate-800/40 border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 flex items-center justify-between hover:border-brand-200 dark:hover:border-brand-600 hover:shadow-md transition-all group">
                            <div class="flex-1 min-w-0 pr-4">
                                <div class="flex items-center space-x-2 mb-1">
                                    <span class="inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border ${typeInfo.color}">
                                        ${typeInfo.label}
                                    </span>
                                    <h4 class="font-bold text-slate-800 dark:text-slate-100 text-sm truncate group-hover:text-brand-600 transition-colors">${escapeHtml(p.title)}</h4>
                                </div>
                                <div class="flex items-center space-x-4 text-xs text-slate-400">
                                    <span><i class="fa-solid fa-calculator text-[10px] mr-1 text-slate-400"></i>总分: <strong class="text-slate-600 dark:text-slate-300">${p.total_score}分</strong></span>
                                    <span><i class="fa-solid fa-list-check text-[10px] mr-1 text-slate-400"></i>题目数: <strong class="text-slate-600 dark:text-slate-300">${p.question_count}题</strong></span>
                                    <span><i class="fa-regular fa-clock text-[10px] mr-1 text-slate-400"></i>${dateStr}</span>
                                </div>
                                ${p.subtitle ? `<p class="text-xs text-slate-400 mt-1 truncate italic">备注: ${escapeHtml(p.subtitle)}</p>` : ''}
                            </div>
                            <div class="flex items-center space-x-2 shrink-0">
                                <button onclick="loadSavedPaper(${p.id})" class="px-3 py-1.5 rounded-xl bg-brand-500 hover:bg-brand-600 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="载入试卷至工作台">
                                    <i class="fa-solid fa-arrow-right-to-bracket text-[11px]"></i>
                                    <span>载入试卷</span>
                                </button>
                                <button onclick="quickExportPaperPdf(${p.id})" class="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="快速编译 PDF">
                                    <i class="fa-solid fa-file-pdf text-[11px]"></i>
                                    <span>PDF</span>
                                </button>
                                <button onclick="deleteSavedPaper(${p.id})" class="p-1.5 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors" title="删除此保存试卷">
                                    <i class="fa-solid fa-trash-can text-xs"></i>
                                </button>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        } catch (e) {
            console.error(e);
            const container = document.getElementById('savedPapersListContainer');
            if (container) {
                container.innerHTML = `<div class="text-center py-12 text-rose-500 text-xs">加载历史试卷失败: ${escapeHtml(e.message)}</div>`;
            }
        }
    };

    window.loadSavedPaper = async function (paperId) {
        try {
            if (window.showToast) window.showToast('正在载入试卷数据...', 'info');

            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;

                // Update metadata
                window.PaperStore.meta.title = paper.title || '未命名试卷';
                window.PaperStore.meta.subtitle = paper.subtitle || '';
                window.PaperStore.meta.paper_type = paper.paper_type || 'exam';
                window.PaperStore.meta.section_order = normalizeSectionOrder(paper.section_order);
                window.PaperStore.meta.show_notice = paper.show_notice !== false;
                window.PaperStore.meta.show_secret = paper.show_secret !== false;

                // Rebuild cart & questionsMap
                window.PaperStore.cart = [];
                if (paper.questions && paper.questions.length > 0) {
                    paper.questions.forEach(item => {
                        const qObj = item.question;
                        window.PaperStore.questionsMap[qObj.id] = qObj;
                        window.PaperStore.cart.push({
                            id: qObj.id,
                            score: item.score || 5
                        });
                    });
                }
                window.PaperStore.streamPagination.selected.page = 1;
                window.PaperStore.cartQuestionLoad = {
                    loading: false,
                    error: '',
                    missingIds: [],
                    confirmedMissingIds: [],
                    failedIds: []
                };
                saveCartToStorage();
                saveMetaToStorage();

                // Close through the shared manager so background inert/aria state is restored.
                window.closeSavedPapersModal();

                // Re-render UI
                if (typeof window.renderPaperWorkspace === 'function') {
                    await window.renderPaperWorkspace();
                }
                if (window.showToast) window.showToast(`已成功载入试卷: 《${paper.title}》`, 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '载入试卷失败', 'error');
            }
        } catch (e) {
            console.error('Load paper failed:', e);
            if (window.showToast) window.showToast('载入试卷请求失败', 'error');
        }
    };

    window.deleteSavedPaper = async function (paperId) {
        if (!confirm('确定要删除这份历史试卷记录吗？（不会影响题库中的题目数据）')) return;

        try {
            const res = await fetch(`/api/papers/${paperId}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('历史试卷已删除', 'success');
                window.openSavedPapersModal();
            } else {
                if (window.showToast) window.showToast(data.message || '删除失败', 'error');
            }
        } catch (e) {
            console.error('Delete paper failed:', e);
            if (window.showToast) window.showToast('删除试卷请求失败', 'error');
        }
    };

    window.quickExportPaperPdf = async function (paperId) {
        const actionKey = `saved-pdf:${paperId}`;
        if (!beginPaperAction(actionKey, '导出历史试卷 PDF')) return;
        try {
            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;
                const cartQuestions = (paper.questions || []).map(item => ({
                    id: item.id,
                    score: item.score,
                    figure_align: getQuestionFigAlign(item.question),
                    figure_align_custom: Boolean(item.question.figure_align_custom || item.question.custom_figure_align),
                    figure_size: getQuestionFigSize(item.question)
                }));

                const tab = window.open('', '_blank');
                setPdfTabLoadingState(tab, '📄 试卷 PDF 编译中', '📄', `正在在线静默编译《${paper.title}》高清 PDF...`);

                const pdfRes = await fetch('/api/paper/export/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: paper.title,
                        subtitle: paper.subtitle,
                        paper_type: paper.paper_type,
                        section_order: normalizeSectionOrder(paper.section_order),
                        target: 'paper',
                        questions: cartQuestions
                    })
                });

                if (pdfRes.ok && pdfRes.headers.get('content-type')?.includes('application/pdf')) {
                    const blob = await pdfRes.blob();
                    const url = URL.createObjectURL(blob);
                    if (tab && !tab.closed) {
                        tab.location.href = url;
                    }
                } else {
                    let errorData = {};
                    try { errorData = await pdfRes.json(); } catch (e) {}
                    if (tab && !tab.closed) {
                        setPdfTabErrorState(tab, '试卷 PDF 编译失败', errorData.diagnostic, errorData.message || '编译 PDF 失败');
                    }
                    if (window.showToast) window.showToast(errorData.message || '编译 PDF 失败', 'error');
                }
            }
        } catch (e) {
            console.error('Quick export PDF failed:', e);
        } finally {
            finishPaperAction(actionKey);
        }
    };

    // Dynamic Helper utilities
    function escapeHtml(str) {
        return window.MathBankSafe.escapeText(str);
    }

    function isWrittenQuestionType(type) {
        return !['single_choice', 'multi_choice', 'fill_in_blank'].includes(type || 'single_choice');
    }

    function normalizeSectionOrder(value) {
        return Array.isArray(value) ? [...new Set(value.filter(type => typeof type === 'string' && type.length > 0))] : [];
    }

    function getPaperTypeOrder(presentTypes, paperType, sectionOrder = []) {
        const order = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];
        if (paperType === 'exam_19') return order;
        const configured = window.systemMetadata && Array.isArray(window.systemMetadata.question_types)
            ? window.systemMetadata.question_types : [];
        configured.forEach(item => {
            if (item && typeof item.value === 'string' && presentTypes.includes(item.value) && !order.includes(item.value)) {
                order.push(item.value);
            }
        });
        presentTypes.forEach(type => {
            if (!order.includes(type)) order.push(type);
        });
        const preferred = normalizeSectionOrder(sectionOrder);
        return preferred.concat(order.filter(type => !preferred.includes(type)));
    }

    function getQuestionTypeCn(type) {
        if (!type) return '题目';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.question_types)) {
            const found = window.systemMetadata.question_types.find(t => t && t.value === type && typeof t.label === 'string' && t.label.trim());
            if (found) return found.label;
        }
        const map = {
            single_choice: '单选题',
            multi_choice: '多选题',
            fill_in_blank: '填空题',
            detailed_answer: '解答题'
        };
        return Object.prototype.hasOwnProperty.call(map, type) ? map[type] : type;
    }

    function getDifficultyBadge(diff) {
        if (!diff) return '';
        let label = diff;
        let colorClass = '';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.difficulties)) {
            const found = window.systemMetadata.difficulties.find(d => d.value === diff);
            if (found) {
                label = found.label;
                if (found.color) {
                    colorClass = found.color;
                }
            }
        } else {
            const fallbackMap = {
                easy: '普通题',
                easy_error: '易错题',
                medium: '挑战题',
                challenge: '挑战题',
                hard: '强基题',
                qiangji: '强基题'
            };
            label = fallbackMap[diff] || diff;
        }

        if (!colorClass) {
            if (typeof window.getDifficultyColor === 'function') {
                colorClass = window.getDifficultyColor(diff);
            } else if (diff === 'easy' || diff === 'normal') {
                colorClass = 'text-blue-600 bg-blue-50 border border-blue-200/60 dark:bg-blue-900/30 dark:text-blue-300';
            } else if (diff === 'easy_error') {
                colorClass = 'text-green-600 bg-green-50 border border-green-200/60 dark:bg-green-900/30 dark:text-green-300';
            } else if (diff === 'hard' || diff === 'qiangji') {
                colorClass = 'text-purple-600 bg-purple-50 border border-purple-200/60 dark:bg-purple-900/30 dark:text-purple-300';
            } else if (diff === 'challenge') {
                colorClass = 'text-red-600 bg-red-50 border border-red-200/60 dark:bg-red-900/30 dark:text-red-300';
            } else {
                colorClass = 'text-slate-600 bg-slate-100 border border-slate-200/60';
            }
        }

        const cleanLabel = String(label || '').replace(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD00-\uDFFF]/g, '').trim();
        colorClass = window.MathBankSafe.safeClassList(colorClass, 'text-slate-600 bg-slate-100 border border-slate-200/60');
        return `<span class="px-2 py-0.5 rounded-lg text-xs font-semibold ${colorClass}">${escapeHtml(cleanLabel)}</span>`;
    }

    const figureLayoutWrites = Object.create(null);
    const figureLayoutMutationRevision = Object.create(null);

    function persistedFigureAlign(q) {
        const value = String(q && q.figure_align || 'right');
        return ['right', 'bottom_left', 'center', 'bottom_right'].includes(value) ? value : 'right';
    }

    function snapshotFigureLayoutsForBankFetch() {
        const revisions = { ...figureLayoutMutationRevision };
        const pending = Object.create(null);
        Object.keys(figureLayoutWrites).forEach(key => {
            const state = figureLayoutWrites[key];
            const q = window.PaperStore.questionsMap[key];
            if (!state || state.pending <= 0 || !q) return;
            pending[key] = {
                figure_align: persistedFigureAlign(q),
                custom_figure_align: q.custom_figure_align,
                figure_align_custom: Boolean(q.figure_align_custom),
                figure_size: getQuestionFigSize(q)
            };
        });
        return { revisions, pending };
    }

    function preserveNewerFigureLayout(question, snapshot) {
        const qid = parseInt(question && question.id, 10);
        if (!qid || !snapshot) return;
        const revisionChanged = (figureLayoutMutationRevision[qid] || 0)
            !== (snapshot.revisions[qid] || 0);
        const pendingLayout = snapshot.pending[qid];
        if (!revisionChanged && !pendingLayout) return;
        const current = window.PaperStore.questionsMap[qid];
        const source = current || pendingLayout;
        if (!source) return;
        question.figure_align = persistedFigureAlign(source);
        question.custom_figure_align = source.custom_figure_align;
        question.figure_align_custom = Boolean(source.figure_align_custom || source.custom_figure_align);
        question.figure_size = getQuestionFigSize(source);
    }

    function getFigureLayoutWriteState(qid, q) {
        if (!figureLayoutWrites[qid]) {
            figureLayoutWrites[qid] = {
                revision: 0,
                pending: 0,
                tail: Promise.resolve(),
                settled: Promise.resolve(),
                confirmed: {
                    figure_align: persistedFigureAlign(q),
                    custom_figure_align: q.custom_figure_align,
                    figure_align_custom: Boolean(q.figure_align_custom),
                    figure_size: getQuestionFigSize(q)
                }
            };
        }
        return figureLayoutWrites[qid];
    }

    window.waitForFigureLayoutWrite = async function(qid) {
        const normalizedId = parseInt(qid, 10);
        if (!normalizedId) return;
        while (true) {
            const state = figureLayoutWrites[normalizedId];
            if (!state || state.pending <= 0) return;
            const observed = state.settled;
            await observed.catch(() => undefined);
            const latest = figureLayoutWrites[normalizedId];
            if (!latest || latest.pending <= 0) return;
        }
    };

    window.hasPendingFigureLayoutWrite = function(qid) {
        const normalizedId = parseInt(qid, 10);
        const state = normalizedId ? figureLayoutWrites[normalizedId] : null;
        return Boolean(state && state.pending > 0);
    };

    window.getFigureLayoutMutationRevision = function(qid) {
        const normalizedId = parseInt(qid, 10);
        return normalizedId ? (figureLayoutMutationRevision[normalizedId] || 0) : 0;
    };

    window.getCurrentQuestionFigureLayout = function(qid) {
        const normalizedId = parseInt(qid, 10);
        const question = normalizedId ? window.PaperStore.questionsMap[normalizedId] : null;
        return question ? {
            figure_align: persistedFigureAlign(question),
            figure_align_custom: Boolean(question.figure_align_custom || question.custom_figure_align),
            figure_size: getQuestionFigSize(question)
        } : null;
    };

    function applyQuestionFigureLayout(question, layout) {
        if (!question || !layout) return;
        question.figure_align = persistedFigureAlign(layout);
        question.custom_figure_align = layout.custom_figure_align;
        question.figure_align_custom = Boolean(layout.figure_align_custom || layout.custom_figure_align);
        question.figure_size = normalizeFigureSize(layout.figure_size);
    }

    function rerenderFigureLayoutPreviews() {
        if (typeof window.renderPart3QuestionStream === 'function') {
            window.renderPart3QuestionStream();
        }
        if (typeof window.renderPaperCanvas === 'function') {
            window.renderPaperCanvas();
        }
    }

    function refreshCurrentEditorFigureLayout(qid) {
        if (!window.EditorState || window.EditorState.questionId !== qid) return;
        if (typeof window.renderIllustrationBadges === 'function') {
            window.renderIllustrationBadges();
        }
        const textarea = document.getElementById('editContent');
        if (textarea) textarea.dispatchEvent(new Event('input'));
    }

    function syncCurrentEditorFigureLayout(qid, layout, commitBaseline = false, expectedCurrent = null) {
        if (!window.EditorState || window.EditorState.questionId !== qid || !window.FigureLayoutState) {
            return;
        }
        const align = persistedFigureAlign(layout);
        const size = normalizeFigureSize(layout && layout.figure_size);
        const customAlign = Boolean(
            layout && (layout.figure_align_custom || layout.custom_figure_align)
        );
        if (commitBaseline && typeof window.commitEditorFigureLayoutBaseline === 'function') {
            window.commitEditorFigureLayoutBaseline(qid, align, size, customAlign);
        }
        if (expectedCurrent && typeof window.FigureLayoutState.snapshot === 'function') {
            const editorLayout = window.FigureLayoutState.snapshot();
            if (editorLayout.figure_align !== persistedFigureAlign(expectedCurrent)
                    || editorLayout.figure_size !== normalizeFigureSize(expectedCurrent.figure_size)
                    || Boolean(editorLayout.figure_align_custom) !== Boolean(
                        expectedCurrent.figure_align_custom || expectedCurrent.custom_figure_align
                    )) {
                return;
            }
        }
        window.FigureLayoutState.setAlign(align);
        window.FigureLayoutState.setSize(size);
        window.FigureLayoutState.setCustomAlign(customAlign);
        refreshCurrentEditorFigureLayout(qid);
    }

    window.setFigureLayout = function (qid, alignVal, sizeVal) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        const q = window.PaperStore.questionsMap[qid];
        if (!q) return;
        if (typeof window.isQuestionSaveInFlight === 'function' && window.isQuestionSaveInFlight()) {
            if (window.showToast) window.showToast('题目正在保存，请稍候再调整插图排版。', 'info');
            return;
        }

        const allowedAlignments = ['right', 'bottom_left', 'center', 'bottom_right'];
        const nextAlign = allowedAlignments.includes(alignVal) ? alignVal : getQuestionFigAlign(q);
        const nextSize = normalizeFigureSize(sizeVal);
        const writeState = getFigureLayoutWriteState(qid, q);
        const requestSequence = writeState.revision + 1;
        writeState.revision = requestSequence;
        writeState.pending += 1;
        figureLayoutMutationRevision[qid] = (figureLayoutMutationRevision[qid] || 0) + 1;
        const requestedLayout = {
            figure_align: nextAlign,
            custom_figure_align: nextAlign,
            figure_align_custom: true,
            figure_size: nextSize
        };

        // Optimistically update both layout dimensions so position and size never
        // drift apart between the bank card and the A4 canvas.
        applyQuestionFigureLayout(q, requestedLayout);
        syncCurrentEditorFigureLayout(qid, requestedLayout);

        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();
        rerenderFigureLayoutPreviews();

        const formData = new FormData();
        formData.append('figure_align', nextAlign);
        formData.append('figure_size', nextSize);

        const requestPromise = writeState.tail.catch(() => undefined).then(() => fetch(
            `/api/questions/${qid}/figure_layout`,
            { method: 'POST', body: formData }
        )).then(async res => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.detail || data.message || `HTTP ${res.status}`);
            }
            return data;
        });
        writeState.tail = requestPromise;

        writeState.settled = requestPromise.then(data => {
            writeState.confirmed = {
                figure_align: allowedAlignments.includes(data.figure_align) ? data.figure_align : nextAlign,
                custom_figure_align: allowedAlignments.includes(data.figure_align) ? data.figure_align : nextAlign,
                figure_align_custom: data.figure_align_custom !== false,
                figure_size: normalizeFigureSize(data.figure_size || nextSize)
            };
            const editorReconciled = typeof window.reconcileEditorFigureLayout === 'function'
                && window.reconcileEditorFigureLayout(
                    qid,
                    writeState.confirmed,
                    requestedLayout,
                    true
                );
            if (!editorReconciled && typeof window.commitEditorFigureLayoutBaseline === 'function'
                    && window.EditorState && window.EditorState.questionId === qid) {
                window.commitEditorFigureLayoutBaseline(
                    qid,
                    writeState.confirmed.figure_align,
                    writeState.confirmed.figure_size,
                    writeState.confirmed.figure_align_custom
                );
            }
            if (writeState.revision !== requestSequence) return;
            const currentQuestion = window.PaperStore.questionsMap[qid];
            applyQuestionFigureLayout(currentQuestion, writeState.confirmed);
            if (editorReconciled) refreshCurrentEditorFigureLayout(qid);
            else syncCurrentEditorFigureLayout(qid, writeState.confirmed, true, requestedLayout);
            rerenderFigureLayoutPreviews();
            const alignLabels = {
                'right': '题干右侧',
                'bottom_left': '下方居左',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const seqNum = currentQuestion && currentQuestion.seq_num !== undefined
                ? currentQuestion.seq_num
                : qid;
            if (window.showToast) {
                window.showToast(`已调整题目 #${seqNum} 插图：${alignLabels[writeState.confirmed.figure_align]} · ${FIGURE_SIZE_LABELS[writeState.confirmed.figure_size]}`, 'success');
            }
        })
        .catch(err => {
            if (writeState.revision !== requestSequence) return;
            const currentQuestion = window.PaperStore.questionsMap[qid];
            applyQuestionFigureLayout(currentQuestion, writeState.confirmed);
            const editorReconciled = typeof window.reconcileEditorFigureLayout === 'function'
                && window.reconcileEditorFigureLayout(
                    qid,
                    writeState.confirmed,
                    requestedLayout,
                    false
                );
            if (editorReconciled) refreshCurrentEditorFigureLayout(qid);
            else syncCurrentEditorFigureLayout(qid, writeState.confirmed, false, requestedLayout);
            rerenderFigureLayoutPreviews();
            console.error('Update figure layout failed:', err);
            if (window.showToast) window.showToast(`插图排版保存失败：${err.message}`, 'error');
        })
        .finally(() => {
            writeState.pending = Math.max(0, writeState.pending - 1);
            if (writeState.pending === 0 && writeState.revision === requestSequence) {
                delete figureLayoutWrites[qid];
            }
        });
    };

    window.setFigureAlign = function (qid, alignVal) {
        const q = window.PaperStore.questionsMap[parseInt(qid, 10)] || {};
        const currentSize = getQuestionFigSize(q);
        const nextSize = alignVal === 'right' && ['medium', 'large'].includes(currentSize)
            ? 'small'
            : currentSize;
        window.setFigureLayout(qid, alignVal, nextSize);
    };

    window.setFigureSize = function (qid, sizeVal) {
        const q = window.PaperStore.questionsMap[parseInt(qid, 10)] || {};
        const currentAlign = getQuestionFigAlign(q);
        const nextAlign = currentAlign === 'right' && ['medium', 'large'].includes(sizeVal)
            ? 'bottom_right'
            : currentAlign;
        window.setFigureLayout(qid, nextAlign, sizeVal);
    };

    window.showFigureAlignPopover = function (event, qid) {
        event.preventDefault();
        event.stopPropagation();

        qid = parseInt(qid, 10);
        const q = window.PaperStore.questionsMap[qid] || {};
        const currentAlign = getQuestionFigAlign(q);
        const currentSize = getQuestionFigSize(q);

        // Remove existing popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        const popover = document.createElement('div');
        popover.id = 'figureAlignPopoverMenu';
        popover.setAttribute('role', 'dialog');
        popover.setAttribute('aria-label', '调整插图排版');
        popover.className = 'fixed z-50 w-56 bg-white/95 backdrop-blur-md rounded-2xl border border-slate-200 shadow-xl p-2 font-sans text-xs flex flex-col space-y-1 animate-in fade-in zoom-in-95 duration-150 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-100';

        // Position popover near mouse cursor
        let left = event.clientX + 5;
        let top = event.clientY + 5;

        // Keep inside viewport bounds
        if (left + 220 > window.innerWidth) left = window.innerWidth - 230;
        if (top + 265 > window.innerHeight) top = window.innerHeight - 275;

        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;

        popover.innerHTML = `
            <div class="px-2 py-1 text-[11px] font-bold text-slate-400 border-b border-slate-100 dark:border-slate-700 flex items-center justify-between">
                <span><i class="fa-solid fa-sliders text-brand-500 mr-1"></i> 调整插图排版</span>
                <button onclick="document.getElementById('figureAlignPopoverMenu').remove()" aria-label="关闭插图排版" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <button onclick="window.setFigureAlign(${qid}, 'right')" aria-label="插图位置：题干右侧" aria-pressed="${currentAlign === 'right' ? 'true' : 'false'}" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干右侧 (默认)</span>
                ${currentAlign === 'right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'bottom_left')" aria-label="插图位置：题干下方居左" aria-pressed="${currentAlign === 'bottom_left' ? 'true' : 'false'}" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'bottom_left' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-left text-xs mr-2 text-brand-500"></i> 题干下方居左</span>
                ${currentAlign === 'bottom_left' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'center')" aria-label="插图位置：题干下方居中" aria-pressed="${currentAlign === 'center' ? 'true' : 'false'}" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'center' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-center text-xs mr-2 text-brand-500"></i> 题干下方居中</span>
                ${currentAlign === 'center' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'bottom_right')" aria-label="插图位置：题干下方居右" aria-pressed="${currentAlign === 'bottom_right' ? 'true' : 'false'}" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'bottom_right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干下方居右</span>
                ${currentAlign === 'bottom_right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <div class="mt-1 border-t border-slate-100 px-1 pt-2 dark:border-slate-700">
                <div class="mb-1 flex items-center justify-between px-1 text-[10px] text-slate-400">
                    <span>尺寸</span>
                    <span>${currentAlign === 'right' ? '中/大图自动改为下方居右' : '下方布局生效'}</span>
                </div>
                <div class="flex items-center gap-1">
                    ${FIGURE_SIZE_VALUES.map(size => `
                        <button type="button" onclick="window.setFigureSize(${qid}, '${size}')"
                            class="min-w-0 flex-1 rounded-md border px-1.5 py-1 text-center text-[10px] transition-colors ${currentSize === size ? 'border-brand-200 bg-brand-50 font-bold text-brand-700' : 'border-slate-200 bg-white text-slate-500 hover:border-brand-200 hover:text-brand-600 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300'}"
                            title="${currentAlign === 'right' && ['medium', 'large'].includes(size) ? '为避免挤压题干，将自动改为下方居右' : `插图尺寸：${FIGURE_SIZE_LABELS[size]}`}"
                            aria-label="插图尺寸：${FIGURE_SIZE_LABELS[size]}" aria-pressed="${currentSize === size ? 'true' : 'false'}">${FIGURE_SIZE_LABELS[size]}</button>
                    `).join('')}
                </div>
            </div>
        `;

        document.body.appendChild(popover);

        // Click outside listener
        const closeHandler = function (e) {
            if (!popover.contains(e.target)) {
                popover.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeHandler);
        }, 50);
    };

    function handleFigureAlignImageEvent(event) {
        const image = event.target && event.target.closest
            ? event.target.closest('img[data-figure-align-qid]')
            : null;
        if (!image) return;
        const qid = parseInt(image.dataset.figureAlignQid, 10);
        if (!qid) return;
        event.preventDefault();
        event.stopPropagation();
        window.showFigureAlignPopover(event, qid);
    }

    document.addEventListener('click', handleFigureAlignImageEvent);
    document.addEventListener('contextmenu', handleFigureAlignImageEvent);

    function splitChoiceContentForPaperPreview(raw) {
        const source = String(raw || '');
        const beginMarker = '\\begin{choices}';
        const endMarker = '\\end{choices}';
        const beginIndex = source.indexOf(beginMarker);
        if (beginIndex < 0) {
            return { stemRaw: source, choicesRaw: '' };
        }

        const endIndex = source.indexOf(endMarker, beginIndex + beginMarker.length);
        if (endIndex < 0) {
            return { stemRaw: source, choicesRaw: '' };
        }

        const choicesEnd = endIndex + endMarker.length;
        return {
            stemRaw: `${source.slice(0, beginIndex)}\n${source.slice(choicesEnd)}`.trim(),
            choicesRaw: source.slice(beginIndex, choicesEnd).trim()
        };
    }

    function shouldPreserveInlinePaperImages(raw) {
        const source = String(raw || '');
        const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
        const matches = [...source.matchAll(imagePattern)];
        if (matches.length === 0) return false;

        // A trailing image (or a trailing cluster of images) is the legacy
        // detachable figure layout controlled by figure_align.  As soon as
        // authored content continues after the first image, the image is an
        // inline document anchor.  This includes images inside tabular cells,
        // between sub-questions, and before captions or explanatory text.
        const firstImageIndex = matches[0].index || 0;
        const trailingContent = source.slice(firstImageIndex)
            .replace(imagePattern, '')
            .trim();
        return trailingContent.length > 0;
    }

    const FIGURE_SIZE_VALUES = ['auto', 'small', 'medium', 'large'];
    const FIGURE_SIZE_LABELS = {
        auto: '自动',
        small: '小',
        medium: '中',
        large: '大'
    };

    function normalizeFigureSize(value) {
        return FIGURE_SIZE_VALUES.includes(value) ? value : 'auto';
    }

    function getQuestionFigSize(q) {
        return normalizeFigureSize(q && q.figure_size);
    }

    function getFigureDimensions(figSize, figAlign, imageCount) {
        const count = Math.max(1, parseInt(imageCount, 10) || 1);
        if (figAlign === 'right') {
            return count > 1
                ? { maxWidth: 125, maxHeight: 115 }
                : { maxWidth: 155, maxHeight: 135 };
        }

        const size = normalizeFigureSize(figSize);
        if (size === 'medium') return { maxWidth: 320, maxHeight: 240 };
        if (size === 'large') return { maxWidth: 420, maxHeight: 300 };
        if (size === 'auto' && count > 1) return { maxWidth: 150, maxHeight: 140 };
        return { maxWidth: 200, maxHeight: 170 };
    }

    function getDetachedFigureMetrics(raw, figAlign, figSize) {
        const fullSource = String(raw || '');
        const source = window.ImageLayoutTools ? window.ImageLayoutTools.split(fullSource).tail : fullSource;
        if (shouldPreserveInlinePaperImages(source)) {
            return {
                count: 0,
                effectiveAlign: figAlign || 'right',
                maxWidth: 0,
                maxHeight: 0,
                blockHeight: 0
            };
        }

        const sources = [];
        const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
        let match;
        while ((match = imagePattern.exec(source)) !== null) {
            const value = String(match[1] || '').trim();
            if (value && !sources.includes(value)) sources.push(value);
        }
        const count = sources.length;
        const effectiveAlign = count > 1 && figAlign === 'right'
            ? 'center'
            : (figAlign || 'right');
        if (count === 0) {
            return { count, effectiveAlign, maxWidth: 0, maxHeight: 0, blockHeight: 0 };
        }

        let dimensions = getFigureDimensions(figSize, effectiveAlign, count);
        if (normalizeFigureSize(figSize) === 'auto' && count === 1 && effectiveAlign !== 'right') {
            // Natural dimensions are not available until the image loads. Reserve
            // the wide-image ceiling up front so pagination and solution space
            // cannot clip a late auto expansion from 200px to 420px.
            dimensions = { maxWidth: 420, maxHeight: 300 };
        }
        if (effectiveAlign === 'right') {
            return {
                count,
                effectiveAlign,
                ...dimensions,
                blockHeight: Math.max(0, dimensions.maxHeight - 70)
            };
        }

        // The A4 body is about 714px wide. Estimate wrapped rows conservatively
        // so larger figures do not get grouped onto a page that will clip them.
        const columns = Math.max(1, Math.floor(700 / (dimensions.maxWidth + 8)));
        const rows = Math.ceil(count / columns);
        return {
            count,
            effectiveAlign,
            ...dimensions,
            blockHeight: rows * dimensions.maxHeight + Math.max(0, rows - 1) * 8 + 30
        };
    }

    function figureImageStyle(dimensions) {
        return `max-width: min(100%, ${dimensions.maxWidth}px); max-height: ${dimensions.maxHeight}px; width: auto; height: auto;`;
    }

    function applyAutoFigureImageSize(image) {
        if (!image || image.dataset.figureSize !== 'auto') return;
        if (image.dataset.figureAlign === 'right' || image.dataset.figureImageCount !== '1') return;
        if (!(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return;

        const isWide = image.naturalWidth / image.naturalHeight >= 1.6;
        const dimensions = isWide
            ? { maxWidth: 420, maxHeight: 300 }
            : { maxWidth: 200, maxHeight: 170 };
        image.style.maxWidth = `min(100%, ${dimensions.maxWidth}px)`;
        image.style.maxHeight = `${dimensions.maxHeight}px`;
        image.dataset.figureAutoResolved = isWide ? 'wide' : 'standard';
    }

    function initializeAutoFigureSizing(scope) {
        if (!scope || !scope.querySelectorAll) return;
        scope.querySelectorAll('img[data-figure-size="auto"]').forEach(image => {
            if (image.complete) {
                applyAutoFigureImageSize(image);
                return;
            }
            if (image.dataset.figureAutoListening === 'true') return;
            image.dataset.figureAutoListening = 'true';
            image.addEventListener('load', () => applyAutoFigureImageSize(image), { once: true });
        });
    }

    function formatQuestionContentHtml(raw, qid = null, figAlign = 'right', embedInSolSpace = false, showControls = true, figSize = 'auto', imageLayouts = {}) {
        if (!raw) return embedInSolSpace ? { stemHtml: '', imgHtml: null } : '';
        let html = String(raw).trim();
        figAlign = figAlign || 'right';
        figSize = normalizeFigureSize(figSize);

        if (typeof window.cleanChoiceStemParentheses === 'function' && (html.includes('choices') || html.match(/^\s*[-*]?\s*[A-D][\.、\s]/m))) {
            if (html.includes('\\begin{choices}')) {
                const parts = html.split('\\begin{choices}');
                parts[0] = window.cleanChoiceStemParentheses(parts[0]);
                html = parts[0] + '\\begin{choices}' + parts[1];
            } else {
                html = window.cleanChoiceStemParentheses(html);
            }
        }

        const imageParts = window.ImageLayoutTools ? window.ImageLayoutTools.split(html) : null;
        const anchoredHtml = imageParts && imageParts.tail && imageParts.body
            ? window.parseMarkdownWithMath(imageParts.body, imageLayouts) : '';
        if (imageParts && imageParts.tail) html = imageParts.tail;
        if (shouldPreserveInlinePaperImages(html)) {
            const inlineHtml = typeof window.parseMarkdownWithMath === 'function'
                ? window.parseMarkdownWithMath(html, imageLayouts)
                : window.MathBankSafe.sanitizeRichHtml(html);
            if (embedInSolSpace) {
                return {
                    stemHtml: `<div>${inlineHtml}</div>`,
                    imgHtml: null,
                    figAlign: figAlign
                };
            }
            return inlineHtml;
        }

        // 1. Extract ALL Markdown image syntaxes ![](/static/uploads/xxx.png) BEFORE KaTeX processing
        const imgSrcList = [];
        const imgMatches = [...html.matchAll(/!\[.*?\]\(([^)]+)\)/g)];
        imgMatches.forEach(m => {
            const safeSrc = window.MathBankSafe.safeImageUrl(m[1]);
            if (safeSrc && !imgSrcList.includes(safeSrc)) imgSrcList.push(safeSrc);
        });
        html = html.replace(/!\[.*?\]\(([^)]+)\)/g, '').trim();
        
        // 2. Process LaTeX formulas, \underline, choices environment & LaTeX standard paragraphs via preprocessFormulaForKaTeX
        if (typeof window.parseMarkdownWithMath === 'function') {
            html = window.parseMarkdownWithMath(html);
        } else {
            html = window.MathBankSafe.sanitizeRichHtml(html);
        }

        const stemText = anchoredHtml + html;

        if (imgSrcList.length > 0) {
            // 如果存在多张插图且原设定为右侧，默认自动优化调整为下方居中 (center) 展示
            const effectiveAlign = (imgSrcList.length > 1 && figAlign === 'right') ? 'center' : (figAlign || 'right');
            const alignLabelMap = {
                'right': '题干右侧',
                'bottom_left': '下方居左',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const currentLabel = alignLabelMap[effectiveAlign] || '下方居中';
            const currentSizeLabel = FIGURE_SIZE_LABELS[figSize] || FIGURE_SIZE_LABELS.auto;
            const iconClass = effectiveAlign === 'center'
                ? 'fa-align-center'
                : (effectiveAlign === 'bottom_left' ? 'fa-align-left' : 'fa-align-right');
            const qidAttr = parseInt(qid, 10) || 0;
            const countTag = imgSrcList.length > 1 ? ` (${imgSrcList.length}图)` : '';
            const dimensions = getFigureDimensions(figSize, effectiveAlign, imgSrcList.length);
            const imageStyle = figureImageStyle(dimensions);
            const sizingAttrs = `data-figure-size="${figSize}" data-figure-align="${effectiveAlign}" data-figure-image-count="${imgSrcList.length}"`;

            const imgClass = showControls 
                ? 'paper-figure-image max-w-full object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block'
                : 'paper-figure-image max-w-full object-contain rounded-lg border border-slate-200 shadow-sm inline-block';

            const imgsHtml = imgSrcList.map((src, idx) => {
                const controlAttrs = showControls
                    ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                    : '';
                return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${imgClass}" style="${imageStyle}" ${sizingAttrs} ${controlAttrs} loading="lazy" decoding="async">`;
            }).join('');

            const btnHtml = showControls ? `
                <div class="mt-1 ${effectiveAlign === 'center' ? 'text-center' : (effectiveAlign === 'bottom_left' ? 'text-left' : 'text-right')}">
                    <button onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})" class="inline-flex items-center text-[10px] font-sans text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200/80 rounded-md px-1.5 py-0.5 transition-colors shadow-sm">
                        <i class="fa-solid ${iconClass} text-[9px] mr-1 text-brand-500"></i> ${currentLabel} · ${currentSizeLabel}${countTag} <i class="fa-solid fa-chevron-down text-[8px] ml-1 opacity-70"></i>
                    </button>
                </div>
            ` : '';

            const imgControlHtml = `
                <div class="inline-block max-w-full relative group/fig">
                    <div class="flex flex-wrap items-center ${effectiveAlign === 'center' ? 'justify-center' : (effectiveAlign === 'bottom_left' ? 'justify-start' : 'justify-end')} gap-2">
                        ${imgsHtml}
                    </div>
                    ${btnHtml}
                </div>
            `;

            if (embedInSolSpace && ['bottom_left', 'center', 'bottom_right'].includes(effectiveAlign)) {
                return {
                    stemHtml: `<div>${stemText}</div>`,
                    imgHtml: imgControlHtml,
                    figAlign: effectiveAlign
                };
            }

            if (effectiveAlign === 'bottom_left') {
                return `<div>${stemText}</div><div class="my-2 text-left">${imgControlHtml}</div>`;
            } else if (effectiveAlign === 'center') {
                return `<div>${stemText}</div><div class="my-2 text-center">${imgControlHtml}</div>`;
            } else if (effectiveAlign === 'bottom_right') {
                return `<div>${stemText}</div><div class="my-2 text-right">${imgControlHtml}</div>`;
            } else { // default 'right': Give text 70%+ dominant width, constrain figure container to 160px
                const rightImgsHtml = imgSrcList.map((src, idx) => {
                    const controlAttrs = showControls
                        ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                        : '';
                    const rightDimensions = getFigureDimensions(figSize, 'right', imgSrcList.length);
                    const rightImgClass = showControls
                        ? 'paper-figure-image max-w-full object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block'
                        : 'paper-figure-image max-w-full object-contain rounded-lg border border-slate-200 shadow-sm inline-block';
                    return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${rightImgClass}" style="${figureImageStyle(rightDimensions)}" data-figure-size="${figSize}" data-figure-align="right" data-figure-image-count="${imgSrcList.length}" ${controlAttrs} loading="lazy" decoding="async">`;
                }).join('');

                const rightImgControlHtml = `
                    <div class="inline-block max-w-full relative group/fig">
                        <div class="flex flex-wrap items-center justify-end gap-1.5">
                            ${rightImgsHtml}
                        </div>
                        ${btnHtml}
                    </div>
                `;

                return `
                    <div class="flex items-start justify-between gap-3 my-1">
                        <div class="flex-1 min-w-0 pr-1" style="max-width: calc(100% - 170px);">${stemText}</div>
                        <div class="shrink-0 text-right" style="width: 160px; max-width: 160px;">${rightImgControlHtml}</div>
                    </div>
                `;
            }
        }
        
        if (embedInSolSpace) {
            return { stemHtml: stemText, imgHtml: null, figAlign: figAlign };
        }

        return stemText;
    }

    // Init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function () {
        initPaperSplitResizer();
        loadStateFromStorage();
        updateCartBadges();

        // Restore active workspace if same server instance run (page refresh / tab re-open)
        const currentServerId = window.__serverInstanceId || '';
        let savedServerId = '';
        let savedWorkspace = 'dashboard';
        try {
            savedServerId = localStorage.getItem('mathbank_server_instance_id') || '';
            savedWorkspace = localStorage.getItem('mathbank_active_workspace') || 'dashboard';
        } catch (e) { }

        if (currentServerId && savedServerId === currentServerId) {
            if (savedWorkspace === 'dashboard') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('dashboard', '工作台');
                }
            } else if (savedWorkspace === 'paper') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('paper', '组卷排版工作台');
                }
            } else if (savedWorkspace === 'import') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('import', '导入中心');
                }
            } else if (savedWorkspace === 'records') {
                window.openSavedPapersModal();
            } else if (savedWorkspace === 'bank') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('bank', '题库管理');
                }
            }
        } else {
            // Fresh server startup (.command / .bat re-launch) -> reset to dashboard default
            try {
                localStorage.setItem('mathbank_active_workspace', 'dashboard');
                if (currentServerId) {
                    localStorage.setItem('mathbank_server_instance_id', currentServerId);
                }
            } catch (e) { }
        }
        document.documentElement.classList.remove('init-ws-paper');
    });

})();
