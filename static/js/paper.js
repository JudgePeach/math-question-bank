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

    // Global Store State
    window.PaperStore = {
        cart: [], // Array of { id: number, score: number }
        meta: {
            title: '2026年高中数学模拟考试试卷',
            subtitle: '',
            paper_type: 'exam_19',
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
        bankQuestions: [], // Loaded questions from DB based on filters
        questionsMap: {}, // qid -> Question Object
        answerCache: Object.create(null), // qid -> full answer_markdown, loaded on demand
        expandedAnswerIds: new Set(),
        answerLoadingIds: new Set(),
        answerErrors: Object.create(null),
        activeWorkspace: 'bank'
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

    // Workspace View Switcher
    const originalSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof originalSelectWorkspace === 'function') {
            originalSelectWorkspace(workspaceId, workspaceName);
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

        if (workspaceId === 'paper') {
            if (bankSec) bankSec.classList.add('hidden');
            if (toggleSidebarBtn) toggleSidebarBtn.classList.add('hidden');
            if (paperSec) {
                paperSec.classList.remove('hidden');
                window.renderPaperWorkspace();
            }
        } else {
            if (paperSec) paperSec.classList.add('hidden');
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
        if (direction === 'up' && index > 0) {
            const temp = cart[index];
            cart[index] = cart[index - 1];
            cart[index - 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        } else if (direction === 'down' && index < cart.length - 1) {
            const temp = cart[index];
            cart[index] = cart[index + 1];
            cart[index + 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
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

    // Fetch Questions from DB for Question Bank Stream
    async function fetchBankQuestions() {
        const f = window.PaperStore.filters;
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

        try {
            const res = await fetch(`/api/questions?${params.toString()}`);
            const questions = await res.json();
            if (Array.isArray(questions)) {
                window.PaperStore.bankQuestions = questions;
                questions.forEach(q => {
                    window.PaperStore.questionsMap[q.id] = q;
                });
            }
        } catch (e) {
            console.error('Fetch bank questions error:', e);
        }
    }

    // Render Full Paper Workspace (Part 2, Part 3, Part 4)
    window.renderPaperWorkspace = async function () {
        await fetchBankQuestions();
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
            clearTimeout(filterDebounceTimer);
            filterDebounceTimer = setTimeout(async () => {
                await fetchBankQuestions();
                renderPart3QuestionStream();
            }, 300);
        } else {
            fetchBankQuestions().then(() => {
                renderPart3QuestionStream();
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

    // Switch Part 3 Tab ('all' or 'selected')
    window.switchPaperStreamTab = function (tabName) {
        window.PaperStore.filters.tab = tabName;
        renderPart3QuestionStream();
    };

    // Render Part 3: Full-Width Question Stream
    function renderPart3QuestionStream() {
        const container = document.getElementById('paperQuestionStream');
        if (!container) return;

        const cart = window.PaperStore.cart;
        const bankQuestions = window.PaperStore.bankQuestions;
        const currentTab = window.PaperStore.filters.tab || 'all';

        // Prepare list based on tab
        let displayList = [];
        if (currentTab === 'selected') {
            displayList = cart.map(item => window.PaperStore.questionsMap[item.id]).filter(Boolean);
        } else {
            displayList = bankQuestions;
        }
        const hasVisibleExpandedAnswers = displayList.some(
            q => q && window.PaperStore.expandedAnswerIds.has(q.id)
        );

        let html = `
            <!-- Part 3 Stream Header Bar -->
            <div class="flex flex-wrap items-center justify-between gap-2 pb-3 mb-4 border-b border-slate-200/60 dark:border-slate-700/60">
                <div class="flex items-center space-x-1.5 bg-slate-200/60 p-1 rounded-xl dark:bg-slate-800">
                    <button onclick="switchPaperStreamTab('all')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'all' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        全库试题 (${bankQuestions.length})
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

        if (displayList.length === 0) {
            html += `
                <div class="flex flex-col items-center justify-center py-20 bg-white/50 backdrop-blur-md rounded-2xl border border-dashed border-slate-300 dark:bg-slate-800/40 dark:border-slate-700">
                    <div class="w-12 h-12 rounded-2xl bg-brand-50 text-brand-500 flex items-center justify-center text-xl mb-3 dark:bg-slate-800">
                        <i class="fa-solid fa-folder-open"></i>
                    </div>
                    <h4 class="font-semibold text-slate-700 dark:text-slate-200 mb-1">未找到符合条件的题目</h4>
                    <p class="text-xs text-slate-500 max-w-xs text-center">请在上方调节学段、章节、题型、难度或搜索条件。</p>
                </div>
            `;
            container.innerHTML = html;
            return;
        }

        html += `<div class="space-y-4">`;

        displayList.forEach((q, index) => {
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
                                    <button onclick="window.movePaperQuestion(${index}, 'up')" ${index === 0 ? 'disabled' : ''} 
                                        class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="上移">
                                        <i class="fa-solid fa-arrow-up text-xs"></i>
                                    </button>
                                    <button onclick="window.movePaperQuestion(${index}, 'down')" ${index === displayList.length - 1 ? 'disabled' : ''} 
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
                        ${formatQuestionContentHtml(q.content, q.id, getQuestionFigAlign(q), false, false)}
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
    }

    // Render Part 4: Right A4 Canvas & Action Bar
    window.renderPaperCanvas = function () {
        const container = document.getElementById('paperCanvasSection');
        if (!container) return;

        // 保存更新前的 A4 画布与外层 Section 滚动位置，解决重绘导致的视口跳回第一页问题
        const oldSheet = document.getElementById('a4PaperPreviewSheet');
        const savedSheetScrollTop = oldSheet ? oldSheet.scrollTop : 0;
        const savedContainerScrollTop = container ? container.scrollTop : 0;

        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;

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

        container.innerHTML = `
            ${aiAnalysisBanner}
            <!-- Part 1: Top Fixed Control Section (Non-scrolling Studio Panel) -->
            <div class="shrink-0 mb-3">
                <div class="bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl flex flex-col space-y-2.5 shadow-sm">
                    <!-- Row 1: Header Stats & Solution Space Config -->
                    <div class="flex items-center justify-between flex-wrap gap-2 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        <div class="flex items-center space-x-3">
                            <div class="flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-brand-50 text-brand-700 font-bold text-xs border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">
                                <span>总分: ${totalScore} 分</span>
                                <span class="text-slate-400 font-normal">|</span>
                                <span>${totalCount} 题</span>
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
                ${generateA4PaperPagesHtml(cart, meta, totalCount, totalScore)}
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
                    本试卷共 ${totalPages} 页，${totalCount} 题。全卷满分 ${totalScore} 分。考试用时 120 分钟。
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

    function generateA4PaperPagesHtml(cart, meta, totalCount, totalScore) {
        if (cart.length === 0) {
            return `
                <div class="a4-paper-sheet w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none">
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

        const typeOrder = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];
        const grouped = {};

        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            const qType = q.question_type || 'single_choice';
            if (!grouped[qType]) grouped[qType] = [];
            grouped[qType].push(item);
        });

        const blocks = [];
        const secNums = ['一', '二', '三', '四', '五'];
        let secIdx = 0;
        const isExam19 = (meta.paper_type === 'exam_19');
        let globalQIndex = 1;

        typeOrder.forEach(qType => {
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
                    secHeaderText = `${secNum}、解答题`;
                }
            } else {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
                } else {
                    secHeaderText = `${secNum}、解答题：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
                }
            }

            blocks.push({
                type: 'section_title',
                qType: qType,
                html: `
                    <div class="paper-sec-block mb-3" data-qtype="${qType}">
                        <h3 class="font-bold text-[13.5px] font-serif mt-2 mb-2 text-slate-900 leading-snug">${secHeaderText}</h3>
                    </div>
                `,
                estHeight: 40
            });

            items.forEach((item, subIdx) => {
                const q = window.PaperStore.questionsMap[item.id];
                let rawContent = q ? q.content : '';
                const figAlign = getQuestionFigAlign(q);

                let solSpaceCm = 0;
                let isSolSpaceEmbedded = false;

                if (qType === 'detailed_answer') {
                    const defaultFallback = meta.paper_type === 'exam_19' ? '0.0' : '7.0';
                    const defaultSpace = parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : defaultFallback);
                    solSpaceCm = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
                    if (isNaN(solSpaceCm)) solSpaceCm = 0.0;

                    if (solSpaceCm > 0 && (figAlign === 'bottom_right' || figAlign === 'center')) {
                        isSolSpaceEmbedded = true;
                    }
                }

                let contentRes = q ? formatQuestionContentHtml(rawContent, q.id, figAlign, isSolSpaceEmbedded) : '';
                let contentHtml = '';
                let embeddedImgHtml = '';

                if (isSolSpaceEmbedded && typeof contentRes === 'object') {
                    contentHtml = contentRes.stemHtml;
                    embeddedImgHtml = contentRes.imgHtml || '';
                } else {
                    contentHtml = typeof contentRes === 'string' ? contentRes : (contentRes.stemHtml || '');
                }

                let stemLine = '';
                if (qType === 'single_choice' || qType === 'multi_choice') {
                    let stemContent = contentHtml;
                    let choicesGrid = '';
                    if (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid') || contentHtml.includes('grid-cols-')) {
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
                        <div class="flex justify-between items-baseline mb-1">
                            <div class="flex-1">${stemContent}</div>
                            <div class="shrink-0 ml-4 font-serif text-slate-900 font-normal select-none">（ &nbsp; ）</div>
                        </div>
                        ${choicesGrid}
                    `;
                } else {
                    stemLine = contentHtml;
                }

                let solutionBlankHtml = '';
                if (qType === 'detailed_answer') {
                    const spacePx = Math.round(solSpaceCm * 35);
                    const isZero = solSpaceCm <= 0;

                    let embeddedImgContainer = '';
                    if (isSolSpaceEmbedded && embeddedImgHtml) {
                        const posClass = figAlign === 'center' ? 'left-1/2 -translate-x-1/2' : 'right-3';
                        embeddedImgContainer = `
                            <div class="absolute ${posClass} top-2 z-10">
                                ${embeddedImgHtml}
                            </div>
                        `;
                    }

                    const minHeightStyle = (isSolSpaceEmbedded && embeddedImgHtml)
                        ? `min-height: ${Math.max(spacePx, 180)}px; height: ${Math.max(spacePx, 180)}px;`
                        : (isZero ? 'min-height: 20px;' : `height: ${spacePx}px;`);

                    solutionBlankHtml = `
                        <div class="solution-space-zone relative ${isZero ? 'py-1 my-1 border-b border-dashed border-slate-200 hover:border-sky-300' : 'mt-2 mb-1 rounded-lg border border-dashed border-sky-300/80 bg-sky-50/20'} group/blank transition-all" style="${minHeightStyle}">
                            ${embeddedImgContainer}
                            <div class="absolute inset-0 flex items-center justify-center pointer-events-none ${isZero ? 'opacity-0 group-hover/blank:opacity-70' : 'opacity-40 group-hover/blank:opacity-80'} transition-opacity">
                                <span class="text-[10px] font-sans text-sky-700 font-medium tracking-wider select-none">
                                    <i class="fa-solid fa-pen-ruler mr-1"></i> 解答题留白区域 (${solSpaceCm.toFixed(1)} cm)
                                </span>
                            </div>
                            <!-- Inline Controls -->
                            <div class="absolute right-2 ${isZero ? '-top-1' : 'bottom-2'} opacity-0 group-hover/blank:opacity-100 transition-opacity flex items-center space-x-1 bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-sm text-[10px] font-sans select-none z-20">
                                <span class="text-slate-400 mr-1 font-medium">留白微调:</span>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 1cm 留白">
                                    - 1cm
                                </button>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 0.5cm 留白">
                                    - 0.5
                                </button>
                                <span class="px-1.5 font-bold text-brand-600">${solSpaceCm.toFixed(1)} cm</span>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 0.5cm 留白">
                                    + 0.5
                                </button>
                                <button onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 1cm 留白">
                                    + 1cm
                                </button>
                            </div>
                        </div>
                    `;
                }

                const itemHtml = `
                    <div class="paper-q-item group relative text-[13px] leading-normal font-serif p-2 rounded-xl border border-transparent hover:border-brand-200 hover:bg-brand-50/30 transition-all duration-200 cursor-grab active:cursor-grabbing mb-2"
                        draggable="true"
                        data-qid="${q ? q.id : ''}"
                        data-qtype="${qType}"
                        data-sub-index="${subIdx}"
                        ondragstart="onPaperCanvasDragStart(event, ${q ? q.id : 0}, ${subIdx}, '${qType}')"
                        ondragover="onPaperCanvasDragOver(event)"
                        ondragenter="onPaperCanvasDragEnter(event)"
                        ondragleave="onPaperCanvasDragLeave(event)"
                        ondragend="onPaperCanvasDragEnd(event)"
                        ondrop="onPaperCanvasDrop(event)">

                        <!-- Hover Action Bar: Drag Handle & Quick Move/Remove Buttons -->
                        <div class="paper-canvas-toolbar absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-sans select-none z-10">
                            <span class="toolbar-label font-medium mr-0.5"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'up')" ${subIdx === 0 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'down')" ${subIdx === items.length - 1 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="下移">
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

                let estH = 75;
                if (qType === 'detailed_answer') estH = 120 + Math.round(solSpaceCm * 35);
                if (rawContent.length > 200) estH += 60;

                blocks.push({
                    type: 'question',
                    qType: qType,
                    html: itemHtml,
                    estHeight: estH
                });

                globalQIndex++;
            });
        });

        // Group blocks into A4 Page cards
        const pages = [];
        let currentPage = [];
        let currentH = 0;
        const PAGE_1_MAX = 620; // Height budget for Page 1
        const PAGE_N_MAX = 920; // Height budget for Page 2+

        blocks.forEach(blk => {
            const maxH = (pages.length === 0) ? PAGE_1_MAX : PAGE_N_MAX;
            if (currentH + blk.estHeight > maxH && currentPage.length > 0) {
                pages.push(currentPage);
                currentPage = [blk];
                currentH = blk.estHeight;
            } else {
                currentPage.push(blk);
                currentH += blk.estHeight;
            }
        });
        if (currentPage.length > 0) {
            pages.push(currentPage);
        }

        const totalPages = pages.length;

        // Generate A4 Page Sheet DOM Cards
        let pagesHtml = '';
        pages.forEach((pgBlocks, pgIdx) => {
            const isFirstPage = (pgIdx === 0);
            let pgContent = pgBlocks.map(b => b.html).join('');

            pagesHtml += `
                <div class="a4-paper-sheet w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none mb-8">
                    ${isFirstPage ? renderA4Header(meta, totalCount, totalScore, totalPages) : ''}
                    
                    <div class="space-y-1.5 text-[13px]">
                        ${pgContent}
                    </div>

                    <!-- Page Footer -->
                    <div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">
                        数学 &nbsp; 第 ${pgIdx + 1} 页 (共 ${totalPages} 页)
                    </div>
                </div>
            `;
        });

        return pagesHtml;
    }

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

    window.onPaperCanvasDragStart = function (e, qid, subIndex, qType) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (!card) return;

        draggedItemData = { 
            qid: parseInt(qid, 10), 
            fromSubIndex: parseInt(subIndex, 10),
            qType: qType,
            element: card
        };

        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(qid));

        // Create or reuse dynamic drop placeholder
        if (!dragPlaceholder) {
            dragPlaceholder = document.createElement('div');
            dragPlaceholder.className = 'paper-drag-placeholder border-2 border-dashed border-brand-500 bg-brand-50/70 rounded-xl my-2 flex items-center justify-center text-xs font-semibold text-brand-600 shadow-inner transition-all duration-200 select-none';
            dragPlaceholder.style.height = `${Math.max(48, card.offsetHeight - 8)}px`;
            dragPlaceholder.innerHTML = '<span class="flex items-center space-x-1.5"><i class="fa-solid fa-arrow-down-long text-brand-500 animate-bounce"></i> <span>释放在同题型内插入试题</span></span>';
        }

        // Apply drag style to current card after browser creates drag ghost image
        setTimeout(() => {
            if (card) {
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
        if (!targetCard || targetCard === draggedItemData.element) return;

        // Strict boundary: check if targetCard belongs to the SAME question type section!
        const targetQType = targetCard.dataset.qtype;
        if (targetQType !== draggedItemData.qType) {
            // Different question type section! Disallow drag placeholder insertion
            e.dataTransfer.dropEffect = 'none';
            return;
        }

        e.dataTransfer.dropEffect = 'move';
        const rect = targetCard.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;

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

    window.onPaperCanvasDragEnd = function (e) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (card) {
            card.classList.remove('opacity-30', 'scale-[0.98]', 'bg-slate-100');
        }

        // Find new index within the SAME question type section
        if (dragPlaceholder && dragPlaceholder.parentNode && draggedItemData) {
            const container = dragPlaceholder.parentNode;
            const allItems = Array.from(container.children);
            
            let newSubIndex = 0;
            for (let i = 0; i < allItems.length; i++) {
                const child = allItems[i];
                if (child === dragPlaceholder) {
                    break;
                }
                if (child.classList && child.classList.contains('paper-q-item') && child !== draggedItemData.element) {
                    newSubIndex++;
                }
            }

            const fromSubIndex = draggedItemData.fromSubIndex;
            const qType = draggedItemData.qType;
            
            if (dragPlaceholder.parentNode) {
                dragPlaceholder.parentNode.removeChild(dragPlaceholder);
            }

            if (fromSubIndex !== newSubIndex && fromSubIndex >= 0 && newSubIndex >= 0) {
                window.PaperStore.cart = reorderItemsWithinType(window.PaperStore.cart, qType, fromSubIndex, newSubIndex);

                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) window.showToast(`试题顺序已更新`, 'info');
            } else {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        } else if (dragPlaceholder && dragPlaceholder.parentNode) {
            dragPlaceholder.parentNode.removeChild(dragPlaceholder);
        }

        draggedItemData = null;
        dragPlaceholder = null;
    };

    window.onPaperCanvasDrop = function (e) {
        e.preventDefault();
        window.onPaperCanvasDragEnd(e);
    };

    // Solution Space Handlers
    window.updateQuestionSolutionSpace = function (qid, delta) {
        qid = parseInt(qid, 10);
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (!item) return;
        const defaultSpace = parseFloat(window.PaperStore.meta.solution_space_default || '7.0');
        let currentSpace = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
        if (isNaN(currentSpace)) currentSpace = 7.0;
        
        let newSpace = Math.max(0.0, Math.min(15.0, Math.round((currentSpace + delta) * 10) / 10));
        item.solution_space = newSpace.toFixed(1);
        saveCartToStorage();
        window.renderPaperCanvas();
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
        const defaultSpace = (window.PaperStore.meta.solution_space_default || '7.0').toString();
        return cart.map((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id] || {};
            return {
                id: item.id,
                score: item.score,
                order: idx + 1,
                figure_align: getQuestionFigAlign(q),
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

        // Pre-open single tab synchronously during click event -> 100% bypasses popup blockers!
        const tab = window.open('', '_blank');
        setPdfTabLoadingState(tab, `${iconEmoji} ${targetName} PDF 编译中`, iconEmoji, `正在为您在线静默编译 ${targetName} 高清 PDF...`);

        try {
            if (window.showToast) {
                window.showToast(`正在静默编译 ${targetName} PDF...`, 'info');
            }

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
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

    window.exportPaperWord = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 Word 试卷', 'warning');
            return;
        }

        try {
            const isExam19 = window.PaperStore.meta.paper_type === 'exam_19';
            if (window.showToast) {
                window.showToast(isExam19 ? '正在生成 Word 试卷及解析压缩包（正文+解析，不含答题卡）...' : '正在生成 Word 试卷及参考答案压缩包...', 'info');
            }
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: buildCartQuestionsPayload()
            };
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
            const rawTitle = (window.PaperStore.meta.title || '试卷').trim();
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
    };

    window.exportPaperBundle = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法打包导出 LaTeX 资源包', 'warning');
            return;
        }

        try {
            if (window.showToast) window.showToast('正在打包 LaTeX 源码并编译全套 PDF 归档...', 'info');

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
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
        }
    };

    window.savePaperToDb = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法保存试卷', 'warning');
            return;
        }

        try {
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
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
        }
    };

    // ----------------- Saved Papers Archive Library Modal -----------------
    window.closeSavedPapersModal = function () {
        const modal = document.getElementById('savedPapersModal');
        if (!modal) return;
        window.MathBankModal.close(modal);
        modal.remove();
    };

    window.openSavedPapersModal = async function () {
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

                // Close modal
                const modal = document.getElementById('savedPapersModal');
                if (modal) modal.remove();

                // Re-render UI
                if (typeof window.renderPaperWorkspace === 'function') {
                    window.renderPaperWorkspace();
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
        try {
            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;
                const cartQuestions = (paper.questions || []).map(item => ({
                    id: item.id,
                    score: item.score,
                    figure_align: getQuestionFigAlign(item.question)
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
        }
    };

    // Dynamic Helper utilities
    function escapeHtml(str) {
        return window.MathBankSafe.escapeText(str);
    }

    function getQuestionTypeCn(type) {
        if (!type) return '题目';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.question_types)) {
            const found = window.systemMetadata.question_types.find(t => t.value === type);
            if (found) return found.label;
        }
        const map = {
            single_choice: '单选题',
            multi_choice: '多选题',
            fill_in_blank: '填空题',
            detailed_answer: '解答题'
        };
        return map[type] || type;
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

    window.setFigureAlign = function (qid, alignVal) {
        qid = parseInt(qid, 10);
        if (!qid) return;

        // 1. Optimistically update local memory store
        if (window.PaperStore.questionsMap[qid]) {
            window.PaperStore.questionsMap[qid].figure_align = alignVal;
            window.PaperStore.questionsMap[qid].custom_figure_align = alignVal;
        }

        // Close popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        // 2. Optimistically re-render UI IMMEDIATELY for instant visual feedback!
        if (typeof window.renderPart3QuestionStream === 'function') {
            window.renderPart3QuestionStream();
        }
        if (typeof window.renderPaperCanvas === 'function') {
            window.renderPaperCanvas();
        }

        // 3. Send POST API request to persist in DB (api.js monkey-patch automatically attaches X-Local-Token)
        const formData = new FormData();
        formData.append('figure_align', alignVal);

        fetch(`/api/questions/${qid}/figure_align`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                const labelMap = { 'right': '题干右侧', 'center': '下方居中', 'bottom_right': '下方居右' };
                const q = window.PaperStore.questionsMap[qid];
                const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
                if (window.showToast) window.showToast(`已调整题目 #${seqNum} 插图排版为：${labelMap[alignVal] || alignVal}`, 'success');
            }
        })
        .catch(err => {
            console.error('Update figure_align failed:', err);
        });
    };

    window.showFigureAlignPopover = function (event, qid) {
        event.preventDefault();
        event.stopPropagation();

        qid = parseInt(qid, 10);
        const q = window.PaperStore.questionsMap[qid] || {};
        const currentAlign = getQuestionFigAlign(q);

        // Remove existing popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        const popover = document.createElement('div');
        popover.id = 'figureAlignPopoverMenu';
        popover.className = 'fixed z-50 bg-white/95 backdrop-blur-md rounded-2xl border border-slate-200 shadow-xl p-2 font-sans text-xs flex flex-col space-y-1 animate-in fade-in zoom-in-95 duration-150 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-100';

        // Position popover near mouse cursor
        let left = event.clientX + 5;
        let top = event.clientY + 5;

        // Keep inside viewport bounds
        if (left + 170 > window.innerWidth) left = window.innerWidth - 180;
        if (top + 150 > window.innerHeight) top = window.innerHeight - 160;

        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;

        popover.innerHTML = `
            <div class="px-2 py-1 text-[11px] font-bold text-slate-400 border-b border-slate-100 dark:border-slate-700 flex items-center justify-between">
                <span><i class="fa-solid fa-sliders text-brand-500 mr-1"></i> 调整插图排版位置</span>
                <button onclick="document.getElementById('figureAlignPopoverMenu').remove()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <button onclick="window.setFigureAlign(${qid}, 'right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干右侧 (默认)</span>
                ${currentAlign === 'right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'center')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'center' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-center text-xs mr-2 text-brand-500"></i> 题干下方居中</span>
                ${currentAlign === 'center' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'bottom_right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'bottom_right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干下方居右</span>
                ${currentAlign === 'bottom_right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
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

    function formatQuestionContentHtml(raw, qid = null, figAlign = 'right', embedInSolSpace = false, showControls = true) {
        if (!raw) return embedInSolSpace ? { stemHtml: '', imgHtml: null } : '';
        let html = String(raw).trim();
        figAlign = figAlign || 'right';

        if (typeof window.cleanChoiceStemParentheses === 'function' && (html.includes('choices') || html.match(/^\s*[-*]?\s*[A-D][\.、\s]/m))) {
            if (html.includes('\\begin{choices}')) {
                const parts = html.split('\\begin{choices}');
                parts[0] = window.cleanChoiceStemParentheses(parts[0]);
                html = parts[0] + '\\begin{choices}' + parts[1];
            } else {
                html = window.cleanChoiceStemParentheses(html);
            }
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

        const stemText = html;

        if (imgSrcList.length > 0) {
            // 如果存在多张插图且原设定为右侧，默认自动优化调整为下方居中 (center) 展示
            const effectiveAlign = (imgSrcList.length > 1 && figAlign === 'right') ? 'center' : (figAlign || 'right');
            const alignLabelMap = {
                'right': '题干右侧',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const currentLabel = alignLabelMap[effectiveAlign] || '下方居中';
            const iconClass = effectiveAlign === 'center' ? 'fa-align-center' : 'fa-align-right';
            const qidAttr = parseInt(qid, 10) || 0;
            const countTag = imgSrcList.length > 1 ? ` (${imgSrcList.length}图)` : '';

            const imgClass = showControls 
                ? `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                : `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;

            const imgsHtml = imgSrcList.map((src, idx) => {
                const controlAttrs = showControls
                    ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                    : '';
                return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${imgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
            }).join('');

            const btnHtml = showControls ? `
                <div class="mt-1 ${effectiveAlign === 'center' ? 'text-center' : 'text-right'}">
                    <button onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})" class="inline-flex items-center text-[10px] font-sans text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200/80 rounded-md px-1.5 py-0.5 transition-colors shadow-sm">
                        <i class="fa-solid ${iconClass} text-[9px] mr-1 text-brand-500"></i> ${currentLabel}${countTag} <i class="fa-solid fa-chevron-down text-[8px] ml-1 opacity-70"></i>
                    </button>
                </div>
            ` : '';

            const imgControlHtml = `
                <div class="inline-block relative group/fig">
                    <div class="flex flex-wrap items-center ${effectiveAlign === 'center' ? 'justify-center' : 'justify-end'} gap-2">
                        ${imgsHtml}
                    </div>
                    ${btnHtml}
                </div>
            `;

            if (embedInSolSpace && (effectiveAlign === 'center' || effectiveAlign === 'bottom_right')) {
                return {
                    stemHtml: `<div>${stemText}</div>`,
                    imgHtml: imgControlHtml,
                    figAlign: effectiveAlign
                };
            }

            if (effectiveAlign === 'center') {
                return `<div>${stemText}</div><div class="my-2 text-center">${imgControlHtml}</div>`;
            } else if (effectiveAlign === 'bottom_right') {
                return `<div>${stemText}</div><div class="my-2 text-right">${imgControlHtml}</div>`;
            } else { // default 'right': Give text 70%+ dominant width, constrain figure container to 160px
                const rightImgsHtml = imgSrcList.map((src, idx) => {
                    const controlAttrs = showControls
                        ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                        : '';
                    const rightImgClass = showControls
                        ? `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                        : `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;
                    return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${rightImgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
                }).join('');

                const rightImgControlHtml = `
                    <div class="inline-block relative group/fig">
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
        loadStateFromStorage();
        updateCartBadges();

        // Restore active workspace if same server instance run (page refresh / tab re-open)
        const currentServerId = window.__serverInstanceId || '';
        let savedServerId = '';
        let savedWorkspace = 'bank';
        try {
            savedServerId = localStorage.getItem('mathbank_server_instance_id') || '';
            savedWorkspace = localStorage.getItem('mathbank_active_workspace') || 'bank';
        } catch (e) { }

        if (currentServerId && savedServerId === currentServerId) {
            if (savedWorkspace === 'paper') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('paper', '组卷排版工作台');
                }
            }
        } else {
            // Fresh server startup (.command / .bat re-launch) -> reset to bank studio default
            try {
                localStorage.setItem('mathbank_active_workspace', 'bank');
                if (currentServerId) {
                    localStorage.setItem('mathbank_server_instance_id', currentServerId);
                }
            } catch (e) { }
        }
        document.documentElement.classList.remove('init-ws-paper');
    });

})();
