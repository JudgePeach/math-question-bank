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
            paper_type: 'exam_19'
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
            if (window.showToast) window.showToast(`已将题目 #${qid} 加入试卷`, 'success');
            
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
        if (window.showToast) window.showToast(`已将题目 #${qid} 移出试卷`, 'info');
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

        const bankSec = document.getElementById('bankWorkspaceSection');
        const paperSec = document.getElementById('paperWorkspaceSection');

        if (workspaceId === 'paper') {
            if (bankSec) bankSec.classList.add('hidden');
            if (paperSec) {
                paperSec.classList.remove('hidden');
                window.renderPaperWorkspace();
            }
        } else {
            if (paperSec) paperSec.classList.add('hidden');
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
            if (toggleTxt) toggleTxt.textContent = '展开组卷配置与题库筛选';
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
            <div class="space-y-3 bg-white/80 backdrop-blur-md p-4 rounded-2xl border border-slate-200/70 shadow-sm dark:bg-slate-800/80 dark:border-slate-700/70">
                <!-- Top Row: Paper Metadata -->
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div>
                        <label class="block text-2xs font-bold text-slate-500 uppercase mb-1">主标题</label>
                        <input type="text" id="paperMetaTitle" value="${escapeHtml(meta.title)}" 
                            onchange="updatePaperMeta('title', this.value)"
                            class="w-full px-3 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900/60 dark:border-slate-700 dark:text-slate-200" placeholder="如：2026年高中数学模拟考试试卷">
                    </div>
                    <div>
                        <label class="block text-2xs font-bold text-slate-500 uppercase mb-1">副标题 / 备注</label>
                        <input type="text" id="paperMetaSubtitle" value="${escapeHtml(meta.subtitle)}" 
                            onchange="updatePaperMeta('subtitle', this.value)"
                            class="w-full px-3 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900/60 dark:border-slate-700 dark:text-slate-200" placeholder="如：考试时间：120分钟 满分：150分">
                    </div>
                    <div>
                        <label class="block text-2xs font-bold text-slate-500 uppercase mb-1">试卷类型预设</label>
                        <select id="paperMetaType" onchange="updatePaperMeta('paper_type', this.value)"
                            class="w-full px-3 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900/60 dark:border-slate-700 dark:text-slate-200">
                            <option value="exam_19" ${meta.paper_type === 'exam_19' ? 'selected' : ''}>📄 19题高考卷 (含答题卡)</option>
                            <option value="exam" ${meta.paper_type === 'exam' ? 'selected' : ''}>📄 试卷</option>
                            <option value="quiz" ${meta.paper_type === 'quiz' ? 'selected' : ''}>📝 小练</option>
                        </select>
                    </div>
                </div>

                <!-- Middle Row 1: 3-Level Cascade Curriculum Dropdowns (学段 -> 章节 -> 小节/知识点) -->
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-2 border-t border-slate-100 dark:border-slate-700/60">
                    <div>
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">学段</label>
                        <select id="paperFilterCompulsory" onchange="onPaperFilterChange('compulsory', this.value)"
                            class="w-full px-2.5 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200">
                            ${bookOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">章节</label>
                        <select id="paperFilterChapter" onchange="onPaperFilterChange('chapter', this.value)" ${isChapterDisabled ? 'disabled' : ''}
                            class="w-full px-2.5 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none disabled:bg-slate-100 disabled:opacity-60 dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200">
                            ${chapterOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">小节 / 知识点</label>
                        <select id="paperFilterKnowledge" onchange="onPaperFilterChange('knowledge', this.value)" ${isKnowledgeDisabled ? 'disabled' : ''}
                            class="w-full px-2.5 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none disabled:bg-slate-100 disabled:opacity-60 dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200">
                            ${knowledgeOptions}
                        </select>
                    </div>
                </div>

                <!-- Middle Row 2: Question Type, Difficulty & Search Input -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div>
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">题型</label>
                        <select id="paperFilterType" onchange="onPaperFilterChange('question_type', this.value)"
                            class="w-full px-2.5 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200">
                            ${qTypeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">难度</label>
                        <select id="paperFilterDifficulty" onchange="onPaperFilterChange('difficulty', this.value)"
                            class="w-full px-2.5 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200">
                            ${diffOptions}
                        </select>
                    </div>
                    <div class="col-span-2">
                        <label class="block text-2xs font-semibold text-slate-400 mb-0.5">来源 / 自定义标签 / 关键词</label>
                        <div class="relative">
                            <i class="fa-solid fa-magnifying-glass text-slate-400 absolute left-3 top-2.5 text-xs"></i>
                            <input type="text" id="paperFilterKeyword" value="${escapeHtml(f.keyword)}"
                                oninput="onPaperFilterChange('keyword', this.value)"
                                class="w-full pl-8 pr-3 py-1.5 text-xs rounded-xl border border-slate-200 bg-white focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900 dark:border-slate-700 dark:text-slate-200" placeholder="搜索题干内容 / 来源 / 标签 / 批注...">
                        </div>
                    </div>
                </div>

                <!-- Bottom Row: AI Prompt Selection Bar -->
                <div class="pt-2 border-t border-slate-100 dark:border-slate-700/60 flex items-center space-x-2">
                    <div class="relative flex-1">
                        <i class="fa-solid fa-wand-magic-sparkles text-brand-500 absolute left-3 top-2.5 text-xs"></i>
                        <input type="text" id="paperAiPromptInput" 
                            class="w-full pl-8 pr-3 py-1.5 text-xs rounded-xl border border-brand-200/80 bg-brand-50/30 focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-slate-900/80 dark:border-slate-700 dark:text-slate-200" 
                            placeholder="🤖 AI 智能一键抽卷：例如“帮我抽 5 道难度中等的函数选择题”"
                            onkeypress="if(event.key==='Enter') triggerAiPaperSelect()">
                    </div>
                    <button onclick="triggerAiPaperSelect()" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1 shrink-0">
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

    window.updatePaperMeta = function (key, value) {
        window.PaperStore.meta[key] = value;
        saveMetaToStorage();
        window.renderPaperCanvas();
    };

    window.triggerAiPaperSelect = async function () {
        const input = document.getElementById('paperAiPromptInput');
        if (!input || !input.value.trim()) {
            if (window.showToast) window.showToast('请先输入智能抽卷要求', 'warning');
            return;
        }
        const promptText = input.value.trim();

        try {
            if (window.showToast) window.showToast('正在根据要求智能筛选试题...', 'info');
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
                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) window.showToast(`AI 成功挑选并加入了 ${addedCount} 道试题`, 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '抽选试题无结果', 'warning');
            }
        } catch (e) {
            if (window.showToast) window.showToast('AI 抽题请求异常', 'error');
        }
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

        let html = `
            <!-- Part 3 Stream Header Bar -->
            <div class="flex items-center justify-between pb-3 mb-4 border-b border-slate-200/60 dark:border-slate-700/60">
                <div class="flex items-center space-x-1.5 bg-slate-200/60 p-1 rounded-xl dark:bg-slate-800">
                    <button onclick="switchPaperStreamTab('all')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'all' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-300' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        📚 题库全部试题 (${bankQuestions.length})
                    </button>
                    <button onclick="switchPaperStreamTab('selected')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'selected' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-300' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        📄 已选组卷试题 (${cart.length})
                    </button>
                </div>

                ${cart.length > 0 ? `
                    <button onclick="window.clearCart()" class="text-xs font-medium text-slate-400 hover:text-rose-500 transition-colors flex items-center space-x-1">
                        <i class="fa-solid fa-trash-can text-2xs"></i>
                        <span>清空卷面 (${cart.length})</span>
                    </button>
                ` : ''}
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

            const cardBorderClass = inCart 
                ? 'border-brand-500 ring-2 ring-brand-500/20 bg-brand-50/10 dark:border-brand-500/60 dark:bg-brand-950/20' 
                : 'border-slate-200/80 hover:border-brand-300/80 bg-white/80 dark:bg-slate-800/80 dark:border-slate-700/70';

            html += `
                <div class="p-5 rounded-2xl border ${cardBorderClass} shadow-sm hover:shadow-md transition-all">
                    <!-- Card Top Controls Bar -->
                    <div class="flex items-center justify-between pb-3 mb-3 border-b border-slate-100 dark:border-slate-700/60">
                        <div class="flex items-center space-x-2 flex-wrap gap-y-1">
                            <span class="font-bold text-slate-800 dark:text-slate-100 text-sm">#${q.id}</span>
                            <span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-brand-50 text-brand-600 border border-brand-200/50 dark:bg-brand-900/30 dark:text-brand-300 dark:border-brand-800/50">${qTypeLabel}</span>
                            ${diffTag}
                            ${q.category_compulsory ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">${escapeHtml(q.category_compulsory)}</span>` : ''}
                            ${q.category_chapter ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">${escapeHtml(q.category_chapter)}</span>` : ''}
                            <span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400" title="引用次数">🔥 ${usageCount}次</span>
                        </div>

                        <div class="flex items-center space-x-2">
                            ${inCart ? `
                                <!-- Score Selector -->
                                <div class="flex items-center space-x-1 bg-slate-100/80 px-2 py-1 rounded-xl dark:bg-slate-700/50">
                                    <span class="text-xs text-slate-500 font-medium">分值:</span>
                                    <input type="number" min="1" max="100" value="${currentScore}" 
                                        onchange="window.updatePaperQuestionScore(${q.id}, this.value)"
                                        class="w-12 text-center text-xs font-bold bg-white rounded-lg border border-slate-200 focus:outline-none dark:bg-slate-800 dark:border-slate-600 dark:text-slate-200">
                                    <span class="text-xs text-slate-500 font-medium">分</span>
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
                        ${formatQuestionContentHtml(q.content, q.id, q.figure_align || 'right')}
                    </div>
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
                } catch (e) { }
            }
        });
    }

    // Render Part 4: Right A4 Canvas & Action Bar
    window.renderPaperCanvas = function () {
        const container = document.getElementById('paperCanvasSection');
        if (!container) return;

        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;

        const totalScore = cart.reduce((sum, item) => sum + (parseInt(item.score, 10) || 5), 0);
        const totalCount = cart.length;

        // Calculate difficulty ratio
        let easyCount = 0, medCount = 0, hardCount = 0;
        cart.forEach(item => {
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

        container.innerHTML = `
            <!-- Top Studio Action Bar -->
            <div class="bg-white/80 backdrop-blur-md p-3 rounded-2xl border border-slate-200/70 shadow-sm mb-4 flex items-center justify-between flex-wrap gap-2 dark:bg-slate-800/80 dark:border-slate-700/70">
                <div class="flex items-center space-x-3">
                    <div class="flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-brand-50 text-brand-700 font-bold text-xs border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-300 dark:border-brand-800">
                        <span>总分: ${totalScore} 分</span>
                        <span class="text-slate-400 font-normal">|</span>
                        <span>${totalCount} 题</span>
                    </div>
                    <!-- Difficulty ratio bar -->
                    <div class="hidden lg:flex items-center space-x-1 text-xs">
                        <span class="text-slate-400 font-medium">难度比:</span>
                        <div class="w-24 h-2 rounded-full bg-slate-200 overflow-hidden flex dark:bg-slate-700" title="普通题: ${easyPct}% | 挑战题: ${medPct}% | 强基题: ${hardPct}%">
                            <div class="bg-emerald-500 h-full" style="width: ${easyPct}%"></div>
                            <div class="bg-amber-500 h-full" style="width: ${medPct}%"></div>
                            <div class="bg-rose-500 h-full" style="width: ${hardPct}%"></div>
                        </div>
                    </div>
                </div>

                <div class="flex items-center space-x-2">
                    ${totalCount > 0 ? `
                        <button onclick="clearCart()" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-rose-50 text-rose-600 border border-rose-200 hover:bg-rose-100 active:scale-95 transition-all flex items-center space-x-1.5 dark:bg-rose-950/40 dark:border-rose-800 dark:text-rose-300" title="清空当前试卷中的所有已选题目">
                            <i class="fa-solid fa-trash-can"></i>
                            <span>清空卷面</span>
                        </button>
                    ` : ''}
                    ${meta.paper_type === 'exam_19' ? `
                        <button onclick="exportPaperPdf('paper')" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5" title="编译并打开试卷 PDF 预览">
                            <i class="fa-solid fa-file-pdf"></i>
                            <span>编译试卷 PDF</span>
                        </button>
                        <button onclick="exportPaperPdf('sheet')" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-teal-600 text-white shadow-sm hover:bg-teal-700 active:scale-95 transition-all flex items-center space-x-1.5" title="编译并打开 A3 双面答题卡 PDF 预览">
                            <i class="fa-solid fa-file-lines"></i>
                            <span>编译答题卡 PDF</span>
                        </button>
                    ` : `
                        <button onclick="exportPaperPdf('paper')" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5">
                            <i class="fa-solid fa-file-pdf"></i>
                            <span>编译高清 PDF</span>
                        </button>
                    `}
                    <button onclick="exportPaperTex()" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-slate-700 text-white shadow-sm hover:bg-slate-800 active:scale-95 transition-all flex items-center space-x-1.5 dark:bg-slate-700 dark:hover:bg-slate-600">
                        <i class="fa-solid fa-file-zipper"></i>
                        <span>导出 LaTeX 源码</span>
                    </button>
                    <button onclick="savePaperToDb()" class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1.5">
                        <i class="fa-solid fa-floppy-disk"></i>
                        <span>保存试卷</span>
                    </button>
                </div>
            </div>

            <!-- A4 Desk Canvas Paper Container -->
            <div class="flex flex-col items-center pb-10" id="a4PaperPreviewSheet">
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
            } catch (e) { }
        }
    };

    function renderA4Header(meta, totalCount, totalScore, totalPages) {
        const isExamType = (meta.paper_type === 'exam' || meta.paper_type === 'exam_19');
        return `
            <!-- Top Secret Mark Bar -->
            ${isExamType ? `
                <div class="flex justify-between items-center mb-3 text-xs font-serif font-bold text-slate-800 pb-1">
                    <span>绝密★启用前</span>
                </div>
            ` : ''}

            <!-- Exam Header Title & Subject -->
            <div class="text-center mb-3">
                <h1 class="text-2xl font-bold tracking-normal text-slate-900 font-serif mb-1.5">${escapeHtml(meta.title)}</h1>
                <div class="text-xl font-bold text-slate-900 font-serif my-2">数 学</div>
                ${meta.subtitle ? `<div class="text-xs font-semibold text-slate-700 mb-1">${escapeHtml(meta.subtitle)}</div>` : ''}
            </div>

            ${isExamType ? `
                <div class="text-[12px] text-center font-serif text-slate-800 mb-4">
                    本试卷共 ${totalPages} 页，${totalCount} 题。全卷满分 ${totalScore} 分。考试用时 120 分钟。
                </div>

                <!-- Standard LaTeX Notice Block -->
                <div class="mb-5 text-[11.5px] leading-relaxed font-serif text-slate-800">
                    <div class="font-bold mb-1 text-slate-900 text-[12px]">注意事项：</div>
                    <ol class="list-decimal list-inside space-y-0.5 text-slate-800 pl-4">
                        <li>答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。</li>
                        <li>回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。</li>
                        <li>考试结束后，将本试卷和答题卡一并交回。</li>
                    </ol>
                </div>
            ` : ''}
        `;
    }

    function generateA4PaperPagesHtml(cart, meta, totalCount, totalScore) {
        if (cart.length === 0) {
            return `
                <div class="w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none dark:bg-white dark:text-slate-900">
                    ${renderA4Header(meta, totalCount, totalScore, 1)}
                    <div class="text-center py-24 text-slate-400 font-sans text-xs">暂无试题数据，请在左侧点击“加入试卷”添加题目</div>
                    <div class="absolute bottom-5 left-0 right-0 text-center text-xs font-serif text-slate-700 tracking-wider">数学 &nbsp; 第 1 页 (共 1 页)</div>
                </div>
            `;
        }

        const cartItemsWithIndex = cart.map((item, idx) => ({ ...item, cartIndex: idx }));

        const typeOrder = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];
        const grouped = {};

        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            const qType = q ? q.question_type : 'single_choice';
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
            if (qType === 'single_choice') {
                secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
            } else if (qType === 'multi_choice') {
                secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
            } else if (qType === 'fill_in_blank') {
                secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
            } else {
                secHeaderText = `${secNum}、解答题：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
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
                let contentHtml = q ? formatQuestionContentHtml(rawContent, q.id, q.figure_align || 'right') : '';

                let stemLine = '';
                if (qType === 'single_choice' || qType === 'multi_choice') {
                    let stemContent = contentHtml;
                    let choicesGrid = '';
                    if (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid')) {
                        const match = contentHtml.match(/([\s\S]*?)(<(?:div|p)[^>]*class="[^"]*(?:choices-grid|katex-choices-grid)"[\s\S]*)/i);
                        if (match) {
                            stemContent = match[1];
                            choicesGrid = match[2];
                        }
                    }
                    stemContent = stemContent.replace(/（\s*）/g, '').replace(/\(\s*\)/g, '').replace(/\\paren/g, '').trim();

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

                const itemHtml = `
                    <div class="paper-q-item group relative text-[13px] leading-normal font-serif p-2 rounded-xl border border-transparent hover:border-brand-300 hover:bg-brand-50/30 transition-all duration-200 cursor-grab active:cursor-grabbing mb-2"
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
                        <div class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center space-x-1 bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-xs text-2xs font-sans select-none z-10">
                            <span class="text-slate-400 font-medium mr-1"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'up')" ${subIdx === 0 ? 'disabled' : ''} class="p-0.5 text-slate-500 hover:text-brand-600 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'down')" ${subIdx === items.length - 1 ? 'disabled' : ''} class="p-0.5 text-slate-500 hover:text-brand-600 disabled:opacity-30" title="下移">
                                <i class="fa-solid fa-chevron-down"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.removeFromCart(${q ? q.id : 0})" class="p-0.5 text-slate-400 hover:text-rose-600" title="移出试卷">
                                <i class="fa-solid fa-xmark"></i>
                            </button>
                        </div>

                        <div class="flex items-start">
                            <span class="font-bold mr-1 text-slate-900 shrink-0">${globalQIndex}.</span>
                            <div class="inline flex-1">${stemLine}</div>
                        </div>
                    </div>
                `;

                let estH = 75;
                if (qType === 'detailed_answer') estH = 170;
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
                <div class="w-full max-w-[794px] min-h-[1123px] bg-white text-slate-900 px-10 py-12 shadow-2xl rounded-sm border border-slate-300 font-serif leading-relaxed relative overflow-hidden select-none mb-8 dark:bg-white dark:text-slate-900">
                    ${isFirstPage ? renderA4Header(meta, totalCount, totalScore, totalPages) : `
                        <div class="flex justify-between items-center mb-6 text-xs font-serif text-slate-500 border-b border-slate-200 pb-2">
                            <span>绝密★启用前</span>
                            <span class="font-bold">${escapeHtml(meta.title)}</span>
                        </div>
                    `}
                    
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

    function generateA4PaperBodyHtml(cart) {
        if (cart.length === 0) {
            return `<div class="text-center py-20 text-slate-400 font-sans text-xs">暂无试题数据，请在左侧点击“加入试卷”添加题目</div>`;
        }

        const cartItemsWithIndex = cart.map((item, idx) => ({ ...item, cartIndex: idx }));

        const typeOrder = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];
        const grouped = {};

        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            const qType = q ? q.question_type : 'single_choice';
            if (!grouped[qType]) grouped[qType] = [];
            grouped[qType].push(item);
        });

        let html = '';
        const secNums = ['一', '二', '三', '四', '五'];
        let secIdx = 0;
        let globalQIndex = 1;

        typeOrder.forEach(qType => {
            const items = grouped[qType];
            if (!items || items.length === 0) return;

            const secNum = secNums[secIdx] || (secIdx + 1);
            secIdx++;

            const count = items.length;
            const secScore = items.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);
            const unitScore = items[0] ? (parseInt(items[0].score, 10) || 5) : 5;

            let secHeaderText = '';
            if (qType === 'single_choice') {
                secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
            } else if (qType === 'multi_choice') {
                secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
            } else if (qType === 'fill_in_blank') {
                secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
            } else {
                secHeaderText = `${secNum}、解答题：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
            }

            html += `
                <div class="paper-sec-block mb-4" data-qtype="${qType}">
                    <h3 class="font-bold text-[13.5px] font-serif mb-2.5 text-slate-900 leading-snug">
                        ${secHeaderText}
                    </h3>
                    <div class="space-y-2 relative transition-all duration-200">
            `;

            items.forEach((item, subIdx) => {
                const q = window.PaperStore.questionsMap[item.id];
                let contentHtml = q ? formatQuestionContentHtml(q.content, q.id, q.figure_align || 'right') : '';

                let stemLine = '';
                if (qType === 'single_choice' || qType === 'multi_choice') {
                    let stemContent = contentHtml;
                    let choicesGrid = '';
                    if (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid')) {
                        const match = contentHtml.match(/([\s\S]*?)(<(?:div|p)[^>]*class="[^"]*(?:choices-grid|katex-choices-grid)"[\s\S]*)/i);
                        if (match) {
                            stemContent = match[1];
                            choicesGrid = match[2];
                        }
                    }
                    stemContent = stemContent.replace(/（\s*）/g, '').replace(/\(\s*\)/g, '').replace(/\\paren/g, '').trim();

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

                html += `
                    <div class="paper-q-item group relative text-[13px] leading-normal font-serif p-2 rounded-xl border border-transparent hover:border-brand-300 hover:bg-brand-50/30 transition-all duration-200 cursor-grab active:cursor-grabbing"
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
                        <div class="absolute right-2 top-2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center space-x-1 bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-xs text-2xs font-sans select-none z-10">
                            <span class="text-slate-400 font-medium mr-1"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'up')" ${subIdx === 0 ? 'disabled' : ''} class="p-0.5 text-slate-500 hover:text-brand-600 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'down')" ${subIdx === items.length - 1 ? 'disabled' : ''} class="p-0.5 text-slate-500 hover:text-brand-600 disabled:opacity-30" title="下移">
                                <i class="fa-solid fa-chevron-down"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.removeFromCart(${q ? q.id : 0})" class="p-0.5 text-slate-400 hover:text-rose-600" title="移出试卷">
                                <i class="fa-solid fa-xmark"></i>
                            </button>
                        </div>

                        <div class="flex items-start">
                            <span class="font-bold mr-1 text-slate-900 shrink-0">${globalQIndex}.</span>
                            <div class="inline flex-1">${stemLine}</div>
                        </div>
                    </div>
                `;
                globalQIndex++;
            });

            html += `
                    </div>
                </div>
            `;
        });

        return html;
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
            dragPlaceholder.className = 'paper-drag-placeholder border-2 border-dashed border-brand-400 bg-brand-50/70 rounded-xl my-2 flex items-center justify-center text-xs font-semibold text-brand-600 shadow-inner transition-all duration-200 select-none';
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

    // Export PDF, Tex, and Save Handlers
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

            const cartQuestions = cart.map(item => {
                const q = window.PaperStore.questionsMap[item.id] || {};
                return {
                    id: item.id,
                    score: item.score,
                    figure_align: q.figure_align || 'right'
                };
            });

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
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
                if (tab && !tab.closed) tab.close();
                let errLog = `${targetName} PDF 编译失败`;
                try {
                    const errData = await res.json();
                    if (errData.message) errLog = errData.message;
                } catch (e) {}
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
            tab.document.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>${title}</title>
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
                        <div class="icon-box">${iconEmoji}</div>
                        <h2>${title}</h2>
                        <p>${text}</p>
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

    window.exportPaperTex = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 LaTeX 源码', 'warning');
            return;
        }

        try {
            if (window.showToast) window.showToast('正在打包 LaTeX 源码与图片...', 'info');

            const cartQuestions = cart.map(item => {
                const q = window.PaperStore.questionsMap[item.id] || {};
                return {
                    id: item.id,
                    score: item.score,
                    figure_align: q.figure_align || 'right'
                };
            });

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/tex', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `paper_${Date.now()}.zip`;
                a.click();
                URL.revokeObjectURL(url);
                if (window.showToast) window.showToast('LaTeX 源码包导出成功！', 'success');
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

    // Dynamic Helper utilities
    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
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
        if (window.systemMetadata && Array.isArray(window.systemMetadata.difficulties)) {
            const found = window.systemMetadata.difficulties.find(d => d.value === diff);
            if (found) label = found.label;
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

        let badgeClass = 'bg-amber-50 text-amber-600 border-amber-200/50 dark:bg-amber-900/30 dark:text-amber-300';
        if (diff === 'easy' || diff === 'normal') {
            badgeClass = 'bg-emerald-50 text-emerald-600 border-emerald-200/50 dark:bg-emerald-900/30 dark:text-emerald-300';
        } else if (diff === 'easy_error') {
            badgeClass = 'bg-cyan-50 text-cyan-600 border-cyan-200/50 dark:bg-cyan-900/30 dark:text-cyan-300';
        } else if (diff === 'hard' || diff === 'qiangji') {
            badgeClass = 'bg-rose-50 text-rose-600 border-rose-200/50 dark:bg-rose-900/30 dark:text-rose-300';
        }

        return `<span class="px-2 py-0.5 rounded-lg text-xs font-semibold ${badgeClass} border">${escapeHtml(label)}</span>`;
    }

    window.setFigureAlign = function (qid, alignVal) {
        qid = parseInt(qid, 10);
        if (!qid) return;

        // 1. Optimistically update local memory store
        if (window.PaperStore.questionsMap[qid]) {
            window.PaperStore.questionsMap[qid].figure_align = alignVal;
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
                if (window.showToast) window.showToast(`已调整题目 #${qid} 插图排版为：${labelMap[alignVal] || alignVal}`, 'success');
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
        const currentAlign = q.figure_align || 'right';

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

    function formatQuestionContentHtml(raw, qid = null, figAlign = 'right') {
        if (!raw) return '';
        let html = raw.trim();
        figAlign = figAlign || 'right';

        // 1. Extract Markdown image syntax ![](/static/uploads/xxx.png) BEFORE KaTeX processing to avoid corruption
        let imgSrc = null;
        const imgMatch = html.match(/!\[.*?\]\(([^)]+)\)/);
        if (imgMatch) {
            imgSrc = imgMatch[1];
            html = html.replace(/!\[.*?\]\(([^)]+)\)/g, '').trim();
        }
        
        // 2. Process LaTeX formulas, \underline, choices environment via preprocessFormulaForKaTeX
        if (typeof window.preprocessFormulaForKaTeX === 'function') {
            html = window.preprocessFormulaForKaTeX(html);
        }

        const stemText = html.replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>');

        if (imgSrc) {
            const alignLabelMap = {
                'right': '题干右侧',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const currentLabel = alignLabelMap[figAlign] || '题干右侧';
            const iconClass = figAlign === 'center' ? 'fa-align-center' : 'fa-align-right';
            const qidAttr = qid ? qid : 0;

            const imgControlHtml = `
                <div class="inline-block relative group/fig">
                    <img src="${imgSrc}" class="max-w-[200px] max-h-[170px] object-contain rounded-lg border border-slate-200 shadow-xs cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all"
                        onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})"
                        oncontextmenu="event.preventDefault(); event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})"
                        title="点击或右击可切换图片排版位置 (当前: ${currentLabel})">
                    <div class="mt-1 ${figAlign === 'center' ? 'text-center' : 'text-right'}">
                        <button onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})" class="inline-flex items-center text-[10px] font-sans text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200/80 rounded-md px-1.5 py-0.5 transition-colors shadow-2xs">
                            <i class="fa-solid ${iconClass} text-[9px] mr-1 text-brand-500"></i> ${currentLabel} <i class="fa-solid fa-chevron-down text-[8px] ml-1 opacity-70"></i>
                        </button>
                    </div>
                </div>
            `;

            if (figAlign === 'center') {
                return `<div>${stemText}</div><div class="my-2 text-center">${imgControlHtml}</div>`;
            } else if (figAlign === 'bottom_right') {
                return `<div>${stemText}</div><div class="my-2 text-right">${imgControlHtml}</div>`;
            } else { // default 'right'
                return `
                    <div class="flex items-start justify-between gap-3 my-1">
                        <div class="flex-1 min-w-0">${stemText}</div>
                        <div class="shrink-0 text-right">${imgControlHtml}</div>
                    </div>
                `;
            }
        }
        
        return stemText;
    }

    // Init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function () {
        loadStateFromStorage();
        updateCartBadges();
    });

})();
