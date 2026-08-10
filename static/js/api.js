        // CSRF Token protection & window.fetch Monkey Patch
        (() => {
            const originalFetch = window.fetch;
            window.fetch = async function (input, init = {}) {
                init = init || {};
                
                // 1. Try to read local token from window global variable (injected directly into HTML)
                let localToken = window.__localToken || '';
                
                // 2. Try to read local_token from cookie as fallback
                if (!localToken) {
                    const cookies = document.cookie.split(';');
                    for (let cookie of cookies) {
                        const trimmed = cookie.trim();
                        if (!trimmed) continue;
                        const eqIndex = trimmed.indexOf('=');
                        if (eqIndex === -1) continue;
                        const name = trimmed.substring(0, eqIndex);
                        const value = trimmed.substring(eqIndex + 1);
                        if (name === 'local_token') {
                            localToken = value;
                            break;
                        }
                    }
                }
                
                // 3. Fallback to/from localStorage to prevent losing token on cookie clearing/block
                if (!localToken) {
                    try {
                        localToken = localStorage.getItem('local_token') || '';
                    } catch (e) {
                        console.error('Failed to read localToken from localStorage:', e);
                    }
                } else {
                    try {
                        localStorage.setItem('local_token', localToken);
                    } catch (e) {
                        console.error('Failed to save localToken to localStorage:', e);
                    }
                }
                
                if (localToken) {
                    const method = (init.method || 'GET').toUpperCase();
                    if (['POST', 'PUT', 'DELETE'].includes(method)) {
                        init.headers = init.headers || {};
                        if (init.headers instanceof Headers) {
                            init.headers.set('X-Local-Token', localToken);
                        } else if (Array.isArray(init.headers)) {
                            const hasToken = init.headers.some(h => h[0].toLowerCase() === 'x-local-token');
                            if (!hasToken) {
                                init.headers.push(['X-Local-Token', localToken]);
                            }
                        } else {
                            const keys = Object.keys(init.headers);
                            const hasToken = keys.some(k => k.toLowerCase() === 'x-local-token');
                            if (!hasToken) {
                                init.headers['X-Local-Token'] = localToken;
                            }
                        }
                    }
                }
                
                return originalFetch.call(this, input, init);
            };
        })();

        // Single source of truth for the current editor session identity.
        // Editable form values continue to live in the DOM during this low-risk phase.
        const EditorState = (() => {
            const state = {
                questionId: null,
                seqNum: null,
                createdAt: null,
                draftId: null,
                mode: 'new'
            };

            const reset = () => {
                state.questionId = null;
                state.seqNum = null;
                state.createdAt = null;
                state.draftId = null;
                state.mode = 'new';
            };

            const useQuestion = (question) => {
                state.questionId = question && question.id != null ? question.id : null;
                state.seqNum = question && question.seq_num != null ? question.seq_num : null;
                state.createdAt = question && question.created_at ? question.created_at : null;
                state.draftId = null;
                state.mode = state.questionId == null ? 'new' : 'question';
            };

            const useDraft = (draft) => {
                state.questionId = null;
                state.seqNum = null;
                state.createdAt = null;
                state.draftId = draft && draft.id != null ? draft.id : null;
                state.mode = state.draftId == null ? 'new' : 'draft';
            };

            const setDraftId = (draftId) => {
                state.draftId = draftId == null ? null : draftId;
                if (state.questionId == null) {
                    state.mode = state.draftId == null ? 'new' : 'draft';
                }
            };

            const clearDraft = () => {
                state.draftId = null;
                state.mode = state.questionId == null ? 'new' : 'question';
            };

            const snapshot = () => ({ ...state });

            return Object.freeze({
                get questionId() { return state.questionId; },
                get seqNum() { return state.seqNum; },
                get createdAt() { return state.createdAt; },
                get draftId() { return state.draftId; },
                get mode() { return state.mode; },
                reset,
                useQuestion,
                useDraft,
                setDraftId,
                clearDraft,
                snapshot
            });
        })();
        window.EditorState = EditorState;

        // Global variables
        let activeSidebarTab = 'bank'; // 'bank' or 'drafts'
        let categoryTree = {};
        let systemMetadata = { question_types: [], difficulties: [], curriculum: {} };
        let uploadedImages = [];
        let uploadedAnswerImages = [];
        let originalQuestionState = null;
        let contentOcrAbortController = null;
        let answerOcrAbortController = null;
        let aiSolveAbortController = null;
        let aiSolveProgressTimer = null;

        // Global debounce utility
        const debounce = (func, delay) => {
            let timer;
            return (...args) => {
                clearTimeout(timer);
                timer = setTimeout(() => func.apply(this, args), delay);
            };
        };

        // Initialize Split Screen Resizers
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            const msgEl = document.getElementById('toastMessage');
            const iconContainer = document.getElementById('toastIconContainer');
            
            msgEl.textContent = message;
            
            if (type === 'success') {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-green-500/20 text-green-400";
                iconContainer.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (type === 'error') {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-red-500/20 text-red-400";
                iconContainer.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i>';
            } else {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-brand-500/20 text-brand-400";
                iconContainer.innerHTML = '<i class="fa-solid fa-circle-info"></i>';
            }
            
            // Show toast
            toast.classList.remove('translate-y-12', 'opacity-0');
            toast.classList.add('translate-y-0', 'opacity-100');
            
            // Hide after 3.5 seconds
            setTimeout(() => {
                toast.classList.add('translate-y-12', 'opacity-0');
                toast.classList.remove('translate-y-0', 'opacity-100');
            }, 3500);
        }

        let systemPreferEngine = 'siliconflow';
        let systemPreferSolveModel = 'deepseek-v4-pro';
        let systemPreferParseModel = 'deepseek-v4-flash';

        // Fetch Environment Config Settings Status
        function fetchConfigStatus() {
            // Fetch preferred engine configuration from backend to resolve defaults
            fetch('/api/settings')
                .then(r => r.json())
                .then(settings => {
                    systemPreferEngine = settings.prefer_engine || 'siliconflow';
                    systemPreferSolveModel = settings.prefer_solve_model || 'deepseek-v4-pro';
                    systemPreferParseModel = settings.prefer_parse_model || 'deepseek-v4-flash';
                    
                    // Update main page model selector to match preference
                    const mainModelSelect = document.getElementById('aiModelSelect');
                    if (mainModelSelect) {
                        mainModelSelect.value = systemPreferSolveModel;
                    }
                    
                    // Update dropdown descriptions to reflect current default engine
                    updateOcrPlaceholder('content');
                    updateOcrPlaceholder('answer');

                    // Populate settings modal selectors on start as well
                    const solveCfg = parseModelConfig(settings.prefer_solve_model, 'deepseek', 'deepseek-v4-flash');
                    const solveProv = document.getElementById('solveModelProvider');
                    if (solveProv) {
                        solveProv.value = solveCfg.provider;
                        renderModelSelector('solve', solveCfg.provider, solveCfg.model);
                    }
                    
                    const parseCfg = parseModelConfig(settings.prefer_parse_model, 'deepseek', 'deepseek-v4-flash');
                    const parseProv = document.getElementById('parseModelProvider');
                    if (parseProv) {
                        parseProv.value = parseCfg.provider;
                        renderModelSelector('parse', parseCfg.provider, parseCfg.model);
                    }
                    
                    const classifyCfg = parseModelConfig(settings.prefer_classify_model, 'deepseek', 'deepseek-v4-flash');
                    const classifyProv = document.getElementById('classifyModelProvider');
                    if (classifyProv) {
                        classifyProv.value = classifyCfg.provider;
                        renderModelSelector('classify', classifyCfg.provider, classifyCfg.model);
                    }
                    
                    let ocrProvider = settings.prefer_engine || 'siliconflow';
                    if (ocrProvider === 'ali_bailian') ocrProvider = 'bailian';
                    if (ocrProvider === 'zhongzhan') ocrProvider = 'zhongzhan_gpt';
                    const ocrProv = document.getElementById('ocrModelProvider');
                    if (ocrProv) {
                        ocrProv.value = ocrProvider;
                        let ocrModel = "";
                        if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3-VL-8B-Instruct';
                        else if (ocrProvider === 'bailian') ocrModel = settings.ali_bailian_model || 'qwen3-vl-flash';
                        else if (ocrProvider === 'zhongzhan_gpt') ocrModel = settings.zhongzhan_gpt_ocr_model || 'gpt-4o';
                        else if (ocrProvider === 'zhongzhan_claude') ocrModel = settings.zhongzhan_claude_ocr_model || 'claude-3-5-sonnet';
                        renderModelSelector('ocr', ocrProvider, ocrModel);
                    }
                    
                    const drawCfg = parseModelConfig(settings.prefer_draw_model, 'siliconflow', 'Qwen/Qwen3-VL-32B-Instruct');
                    const drawProv = document.getElementById('drawModelProvider');
                    if (drawProv) {
                        drawProv.value = drawCfg.provider;
                        renderModelSelector('draw', drawCfg.provider, drawCfg.model);
                    }
                })
                .catch(err => {
                    console.error('获取偏好识图引擎设置失败:', err);
                });

            // We just call the API settings check or hit general lists to see if dotenv loaded keys
            // In case keys are missing, backend returns descriptive headers
            const indicator = document.getElementById('apiStatusIndicator');
            
            // Simple check by hitting backend
            fetch('/api/questions')
                .then(r => r.json())
                .then(() => {
                    // Config status check done. We can't query secret directly so we inspect indicator.
                    indicator.className = "flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-green-50 text-green-700 border border-green-150";
                    indicator.innerHTML = '<span class="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse"></span><span>题库就绪</span>';
                })
                .catch(() => {
                    indicator.className = "flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-150";
                    indicator.innerHTML = '<span class="h-1.5 w-1.5 rounded-full bg-red-500"></span><span>后台连接中断</span>';
                });
        }

        // 默认预设模型列表
        const MODEL_PRESETS = {
            deepseek: [
                "deepseek-v4-flash",
                "deepseek-v4-pro"
            ],
            siliconflow: [
                "Qwen/Qwen3-VL-32B-Instruct",
                "Qwen/Qwen3-VL-8B-Instruct",
                "deepseek-ai/DeepSeek-V4-Pro",
                "deepseek-ai/DeepSeek-V4-Flash"
            ],
            bailian: [
                "qwen3-vl-flash",
                "qwen-vl-plus",
                "qwen-vl-max",
                "qwen-max",
                "qwen-plus"
            ],
            zhongzhan_gpt: [],
            zhongzhan_claude: []
        };

        // 从 localStorage 恢复自定义模型，或者初始化
        function getModelListForProvider(provider, typeKey = "") {
            let presets = MODEL_PRESETS[provider] || [];
            let customs = [];
            try {
                const customStr = localStorage.getItem(`custom_models_${provider}`);
                if (customStr) {
                    const parsed = JSON.parse(customStr);
                    if (Array.isArray(parsed)) {
                        customs = parsed;
                    }
                }
            } catch (e) {
                console.error(`解析自定义模型列表失败 for ${provider}:`, e);
            }
            let list = Array.from(new Set([...presets, ...customs]));
            if (provider === 'siliconflow' && typeKey === 'ocr') {
                list = list.filter(m => !m.toLowerCase().includes('deepseek'));
            }
            return list;
        }

        function addCustomModelName(provider, typeKey) {
            const newName = prompt(`请输入要为 [${provider}] 新增的模型名称 (如 qwen-max):`);
            if (!newName || !newName.trim()) return;
            
            const trimmed = newName.trim();
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            const customs = customStr ? JSON.parse(customStr) : [];
            
            if (MODEL_PRESETS[provider] && MODEL_PRESETS[provider].includes(trimmed)) {
                alert("该预设模型已存在于列表中！");
                return;
            }
            if (customs.includes(trimmed)) {
                alert("该自定义模型已存在于列表中！");
                return;
            }
            
            customs.push(trimmed);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            
            // 重新渲染选择器并选中新值
            renderModelSelector(typeKey, provider, trimmed);
        }

        function removeCustomModelName(provider, typeKey, modelValue) {
            if (MODEL_PRESETS[provider] && MODEL_PRESETS[provider].includes(modelValue)) {
                alert("预设的核心模型不支持删除，只能删除自定义追加的模型。");
                return;
            }
            if (!confirm(`确认要从列表中删除自定义模型 "${modelValue}" 吗？`)) return;
            
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            if (!customStr) return;
            
            let customs = JSON.parse(customStr);
            customs = customs.filter(m => m !== modelValue);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            
            // 重新渲染选择器，默认选中第一个
            renderModelSelector(typeKey, provider);
        }

        // 中转站独享：记忆保存当前输入的模型
        function saveCustomZhongzhanModel(provider, typeKey) {
            const inputEl = document.getElementById(`settings_${typeKey}_model_input`);
            if (!inputEl) return;
            const newName = inputEl.value.trim();
            if (!newName) {
                alert("请先在输入框中填写您想记录的中转站模型名称！");
                return;
            }
            
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            const customs = customStr ? JSON.parse(customStr) : [];
            
            if (!customs.includes(newName)) {
                customs.push(newName);
                localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
                showToast(`已将模型 "${newName}" 记录至下拉历史列表`, "success");
            } else {
                showToast("该模型已存在于下拉历史中", "info");
            }
            
            // 刷新渲染
            renderModelSelector(typeKey, provider, newName);
        }

        // 中转站独享：从 localStorage 中删除该选项
        function deleteCustomZhongzhanModel(provider, typeKey) {
            const inputEl = document.getElementById(`settings_${typeKey}_model_input`);
            if (!inputEl) return;
            const newName = inputEl.value.trim();
            if (!newName) return;
            
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            if (!customStr) return;
            
            let customs = JSON.parse(customStr);
            if (!customs.includes(newName)) {
                alert(`未在下拉历史中找到模型 "${newName}"`);
                return;
            }
            
            if (!confirm(`确认要将模型 "${newName}" 从下拉历史列表中移除吗？`)) return;
            
            customs = customs.filter(m => m !== newName);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            showToast(`已从下拉历史中移除模型 "${newName}"`, "success");
            
            // 刷新并清空
            renderModelSelector(typeKey, provider, "");
        }

        // 辅助解析解析 "PROVIDER/model" 前缀
        function parseModelConfig(val, defaultProvider, defaultModel) {
            if (val && val.includes("/")) {
                const idx = val.indexOf("/");
                const prov = val.substring(0, idx).toLowerCase();
                const name = val.substring(idx + 1);
                // 校验前缀合理性
                if (['deepseek', 'siliconflow', 'bailian', 'zhongzhan', 'zhongzhan_gpt', 'zhongzhan_claude'].includes(prov)) {
                    let targetProv = prov;
                    if (targetProv === 'zhongzhan') targetProv = 'zhongzhan_gpt'; // 兼容老数据
                    return { provider: targetProv, model: name };
                }
            }
            return { provider: defaultProvider, model: val || defaultModel };
        }

        // 动态装载模型选择/手写输入区域
        function renderModelSelector(typeKey, provider, selectedValue = "") {
            const container = document.getElementById(`${typeKey}ModelValueContainer`);
            if (!container) return;
            
            const isZhongzhan = provider === 'zhongzhan' || provider === 'zhongzhan_gpt' || provider === 'zhongzhan_claude';
            
            if (isZhongzhan) {
                // 中转站模式：将模型名称输入框与推理强度下拉框按照 7:3 比例弹性排列
                let rawModel = selectedValue || "";
                let reasoningEffort = "default";
                
                if (rawModel.includes(":")) {
                    const parts = rawModel.rsplit ? rawModel.rsplit(":", 1) : rawModel.split(":");
                    const lastPart = parts[parts.length - 1].toLowerCase();
                    if (['default', 'low', 'medium', 'high', 'xhigh', 'max'].includes(lastPart)) {
                        reasoningEffort = lastPart;
                        rawModel = parts.slice(0, parts.length - 1).join(":");
                    }
                } else if (rawModel.endsWith(")") && rawModel.includes("(")) {
                    const match = rawModel.match(/^(.*?)\((default|low|medium|high|xhigh|max)\)$/i);
                    if (match) {
                        rawModel = match[1].trim();
                        reasoningEffort = match[2].toLowerCase();
                    }
                }

                const datalistId = `datalist_${typeKey}_${provider}`;
                const models = getModelListForProvider(provider, typeKey);
                let optionsHtml = models.map(m => `<option value="${m}">`).join('');
                
                container.innerHTML = `
                    <div class="flex items-center space-x-1.5 flex-1 min-w-0">
                        <!-- 70% 宽度：模型名称输入框 -->
                        <div class="w-[68%] min-w-0">
                            <input type="text" id="settings_${typeKey}_model_input" list="${datalistId}" 
                                   placeholder="输入模型名称 (如 gpt-5.6-sol)"
                                   value="${rawModel}" class="glass-input w-full px-2.5 py-1.5 rounded-lg text-xs font-mono">
                        </div>
                        <!-- 30% 宽度：推理强度选择框 -->
                        <div class="w-[32%] shrink-0">
                            <select id="settings_${typeKey}_reasoning_select" class="glass-select w-full px-1.5 py-1.5 rounded-lg text-xs font-mono text-slate-700 dark:text-slate-200" title="选择中转站模型的推理强度 (Reasoning Effort)">
                                <option value="default" ${reasoningEffort === 'default' ? 'selected' : ''}></option>
                                <option value="low" ${reasoningEffort === 'low' ? 'selected' : ''}>low</option>
                                <option value="medium" ${reasoningEffort === 'medium' ? 'selected' : ''}>medium</option>
                                <option value="high" ${reasoningEffort === 'high' ? 'selected' : ''}>high</option>
                                <option value="xhigh" ${reasoningEffort === 'xhigh' ? 'selected' : ''}>xhigh</option>
                                <option value="max" ${reasoningEffort === 'max' ? 'selected' : ''}>max</option>
                            </select>
                        </div>
                    </div>
                    <datalist id="${datalistId}">
                        ${optionsHtml}
                    </datalist>
                    <button type="button" onclick="saveCustomZhongzhanModel('${provider}', '${typeKey}')" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="记录当前输入的模型名称">
                        <i class="fa-solid fa-plus"></i>
                    </button>
                    <button type="button" onclick="deleteCustomZhongzhanModel('${provider}', '${typeKey}')" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-red-500 hover:text-red-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="清除历史记录的模型">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
            } else {
                // 下拉菜单列表模式
                const models = getModelListForProvider(provider, typeKey);
                const selectId = `settings_${typeKey}_model_select`;
                
                let optionsHtml = models.map(m => {
                    const isSel = m === selectedValue ? 'selected' : '';
                    return `<option value="${m}" ${isSel}>${m}</option>`;
                }).join('');
                
                container.innerHTML = `
                    <select id="${selectId}" class="glass-select flex-1 px-2.5 py-1.5 rounded-lg text-xs min-w-0">
                        ${optionsHtml}
                    </select>
                    <button type="button" onclick="addCustomModelName('${provider}', '${typeKey}')" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="新增自定义模型">
                        <i class="fa-solid fa-plus"></i>
                    </button>
                    <button type="button" onclick="const val = document.getElementById('${selectId}').value; removeCustomModelName('${provider}', '${typeKey}', val)" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-red-500 hover:text-red-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="删除当前选中的自定义模型">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
            }
        }

        // 全局挂载联动 onchange
        window.onModelProviderChange = function(typeKey) {
            const providerSelect = document.getElementById(`${typeKey}ModelProvider`);
            if (!providerSelect) return;
            const provider = providerSelect.value;
            
            // 默认取个常用模型初始化
            let defVal = "";
            if (provider === 'deepseek') defVal = "deepseek-v4-flash";
            else if (provider === 'siliconflow') {
                defVal = typeKey === 'ocr' ? "Qwen/Qwen3-VL-8B-Instruct" : "deepseek-ai/DeepSeek-V4-Flash";
            } else if (provider === 'bailian') {
                defVal = typeKey === 'ocr' ? "qwen3-vl-flash" : "qwen-max";
            } else if (provider === 'zhongzhan_gpt') {
                defVal = ""; // 默认空白，供用户填写
            } else if (provider === 'zhongzhan_claude') {
                defVal = ""; // 默认空白，供用户填写
            }
            
            renderModelSelector(typeKey, provider, defVal);
        };
        
        // 挂载辅助方法到全局
        window.addCustomModelName = addCustomModelName;
        window.removeCustomModelName = removeCustomModelName;
        window.saveCustomZhongzhanModel = saveCustomZhongzhanModel;
        window.deleteCustomZhongzhanModel = deleteCustomZhongzhanModel;

        // Settings Modal Controls
        function openSettingsModal() {
            if (window.switchSettingsTab) {
                window.switchSettingsTab('api');
            }
            const modal = document.getElementById('settingsModal');
            document.body.classList.add('modal-active');
            
            // 暂时移除状态灯的呼吸闪烁，防止在半透明模糊遮罩后面晃眼闪烁
            const indicatorDot = document.querySelector('#apiStatusIndicator span');
            if (indicatorDot) {
                indicatorDot.classList.remove('animate-pulse');
            }
            
            // Prefill inputs with current active settings from backend
            fetch('/api/settings')
                .then(r => r.json())
                .then(settings => {
                    document.getElementById('settingsDeepseekKey').value = settings.deepseek_key || '';
                    document.getElementById('settingsSiliconflowKey').value = settings.siliconflow_key || '';
                    document.getElementById('settingsAliBailianKey').value = settings.ali_bailian_key || '';
                    
                    document.getElementById('settingsZhongzhanGptKey').value = settings.zhongzhan_gpt_key || '';
                    document.getElementById('settingsZhongzhanGptBaseUrl').value = settings.zhongzhan_gpt_base_url || '';
                    document.getElementById('settingsZhongzhanClaudeKey').value = settings.zhongzhan_claude_key || '';
                    document.getElementById('settingsZhongzhanClaudeBaseUrl').value = settings.zhongzhan_claude_base_url || '';
                    
                    // 1. AI 智能解题模型
                    const solveCfg = parseModelConfig(settings.prefer_solve_model, 'deepseek', 'deepseek-v4-flash');
                    document.getElementById('solveModelProvider').value = solveCfg.provider;
                    renderModelSelector('solve', solveCfg.provider, solveCfg.model);
                    
                    // 2. 试卷智能拆解模型
                    const parseCfg = parseModelConfig(settings.prefer_parse_model, 'deepseek', 'deepseek-v4-flash');
                    document.getElementById('parseModelProvider').value = parseCfg.provider;
                    renderModelSelector('parse', parseCfg.provider, parseCfg.model);
                    
                    // 3. 题目智能分类模型
                    const classifyCfg = parseModelConfig(settings.prefer_classify_model, 'deepseek', 'deepseek-v4-flash');
                    document.getElementById('classifyModelProvider').value = classifyCfg.provider;
                    renderModelSelector('classify', classifyCfg.provider, classifyCfg.model);
                    
                    // 4. 默认公式识图模型
                    let ocrProvider = settings.prefer_engine || 'siliconflow';
                    if (ocrProvider === 'ali_bailian') ocrProvider = 'bailian'; // 前后端对齐
                    if (ocrProvider === 'zhongzhan') ocrProvider = 'zhongzhan_gpt'; // 兼容老数据
                    
                    let ocrModel = "";
                    if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3-VL-8B-Instruct';
                    else if (ocrProvider === 'bailian') ocrModel = settings.ali_bailian_model || 'qwen3-vl-flash';
                    else if (ocrProvider === 'zhongzhan_gpt') ocrModel = settings.zhongzhan_gpt_ocr_model || 'gpt-4o';
                    else if (ocrProvider === 'zhongzhan_claude') ocrModel = settings.zhongzhan_claude_ocr_model || 'claude-3-5-sonnet';
                    
                    document.getElementById('ocrModelProvider').value = ocrProvider;
                    renderModelSelector('ocr', ocrProvider, ocrModel);
                    
                    // 4. 高级 TikZ 绘图模型
                    const drawCfg = parseModelConfig(settings.prefer_draw_model, 'siliconflow', 'Qwen/Qwen3-VL-32B-Instruct');
                    document.getElementById('drawModelProvider').value = drawCfg.provider;
                    renderModelSelector('draw', drawCfg.provider, drawCfg.model);
                })
                .catch(err => {
                    console.error('获取系统配置失败:', err);
                });

            // 同时拉取最新的自定义维度配置 JSON 并填入，防止直接保存时由于未切换 Tab 导致值为空引发校验错误
            fetch('/api/config/metadata')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(data, null, 2);
                })
                .catch(err => {
                    console.error('获取元数据配置失败:', err);
                });
                
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeSettingsModal() {
            const modal = document.getElementById('settingsModal');
            document.body.classList.remove('modal-active');
            
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
                // 弹窗关闭后，如果状态灯是绿色的，恢复呼吸闪烁
                const indicatorDot = document.querySelector('#apiStatusIndicator span.bg-green-500');
                if (indicatorDot) {
                    indicatorDot.classList.add('animate-pulse');
                }
            }, 300);
        }

        // Graceful server shutdown trigger
        function confirmShutdown() {
            if (confirm("确定要关闭本地题库系统吗？关闭后网页端服务将停止运行，您需要重新双击运行桌面的启动脚本才能再次使用。")) {
                showToast("正在关闭后端服务，请稍候...", "info");
                
                // Show a beautiful premium full-screen overlay to block user interactions cleanly
                const overlay = document.createElement('div');
                overlay.className = "fixed inset-0 bg-slate-900/95 backdrop-blur-md flex flex-col items-center justify-center text-white z-[9999] transition-all duration-500 opacity-0";
                overlay.innerHTML = `
                    <div class="p-8 max-w-md text-center space-y-5">
                        <div class="h-16 w-16 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400 mx-auto text-3xl shadow-lg">
                            <i class="fa-solid fa-power-off"></i>
                        </div>
                        <div class="space-y-2">
                            <h2 class="text-xl font-bold tracking-wide font-['Outfit']">本地题库系统已关闭</h2>
                            <p class="text-xs text-slate-400 leading-relaxed">
                                后端服务进程已成功终止退出。现在您可以安全地关闭此浏览器标签页。
                            </p>
                        </div>
                        <div class="border-t border-slate-800 pt-4 text-left">
                            <p class="text-[11px] text-slate-500 mb-2 font-semibold">🔄 如何重新启动系统？</p>
                            <p class="text-[10px] text-slate-400 leading-normal">
                                如果需要重新开始研讨与录题，请再次双击执行工作空间下的 <code class="bg-slate-850 px-1.5 py-0.5 rounded text-red-300 font-mono text-[9px]">启动题库系统.command</code> 脚本即可。
                            </p>
                        </div>
                    </div>
                `;
                document.body.appendChild(overlay);
                setTimeout(() => {
                    overlay.classList.remove('opacity-0');
                }, 50);

                // Send shutdown request to backend
                fetch('/api/shutdown', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        console.log("Server shutdown command sent:", data);
                    })
                    .catch(err => {
                        console.log("Server socket closed as expected during exit:", err);
                    });
            }
        }

        // --- TIKU DATABASE STATISTICS PANEL CONTROLLERS ---
        let globalStatsData = null;

        function saveSettings(e) {
            e.preventDefault();
            const key = document.getElementById('settingsDeepseekKey').value;
            const siliconflowKey = document.getElementById('settingsSiliconflowKey').value;
            const aliBailianKey = document.getElementById('settingsAliBailianKey').value;
            
            const zhongzhanGptKey = document.getElementById('settingsZhongzhanGptKey').value;
            const zhongzhanGptBaseUrl = document.getElementById('settingsZhongzhanGptBaseUrl').value;
            const zhongzhanClaudeKey = document.getElementById('settingsZhongzhanClaudeKey').value;
            const zhongzhanClaudeBaseUrl = document.getElementById('settingsZhongzhanClaudeBaseUrl').value;
            
            // 辅助获取二级选择结果
            function getSelectedModelValue(typeKey, provider) {
                if (provider === 'zhongzhan' || provider === 'zhongzhan_gpt' || provider === 'zhongzhan_claude') {
                    const input = document.getElementById(`settings_${typeKey}_model_input`);
                    const reasoningSelect = document.getElementById(`settings_${typeKey}_reasoning_select`);
                    let mVal = input ? input.value.trim() : '';
                    if (mVal && reasoningSelect) {
                        const effort = reasoningSelect.value;
                        if (effort && effort !== 'default') {
                            mVal = `${mVal}:${effort}`;
                        }
                    }
                    return mVal;
                } else {
                    const select = document.getElementById(`settings_${typeKey}_model_select`);
                    return select ? select.value : '';
                }
            }
            
            // 1. AI 智能解题模型
            const solveProvider = document.getElementById('solveModelProvider').value;
            const solveModel = getSelectedModelValue('solve', solveProvider);
            const preferSolveModel = `${solveProvider.toUpperCase()}/${solveModel}`;
            
            // 2. 试卷智能拆解模型
            const parseProvider = document.getElementById('parseModelProvider').value;
            const parseModel = getSelectedModelValue('parse', parseProvider);
            const preferParseModel = `${parseProvider.toUpperCase()}/${parseModel}`;
            
            // 3. 题目智能分类模型
            const classifyProvider = document.getElementById('classifyModelProvider').value;
            const classifyModel = getSelectedModelValue('classify', classifyProvider);
            const preferClassifyModel = `${classifyProvider.toUpperCase()}/${classifyModel}`;
            
            // 4. 默认公式识图模型 (后端以 prefer_engine + siliconflow_model/ali_bailian_model/zhongzhan_gpt_ocr_model/zhongzhan_claude_ocr_model 区分)
            const ocrProvider = document.getElementById('ocrModelProvider').value;
            const ocrModel = getSelectedModelValue('ocr', ocrProvider);
            let preferEngine = ocrProvider;
            if (preferEngine === 'bailian') preferEngine = 'ali_bailian'; // 与后端对齐
            
            let siliconflowModel = "";
            let aliBailianModel = "";
            let zhongzhanGptOcrModel = "";
            let zhongzhanClaudeOcrModel = "";
            
            if (ocrProvider === 'siliconflow') siliconflowModel = ocrModel;
            else if (ocrProvider === 'bailian') aliBailianModel = ocrModel;
            else if (ocrProvider === 'zhongzhan_gpt') zhongzhanGptOcrModel = ocrModel;
            else if (ocrProvider === 'zhongzhan_claude') zhongzhanClaudeOcrModel = ocrModel;
            
            // 5. 高级 TikZ 绘图模型
            const drawProvider = document.getElementById('drawModelProvider').value;
            const drawModel = getSelectedModelValue('draw', drawProvider);
            const preferDrawModel = `${drawProvider.toUpperCase()}/${drawModel}`;
            
            const formData = new FormData();
            formData.append('deepseek_key', key);
            formData.append('siliconflow_key', siliconflowKey);
            formData.append('ali_bailian_key', aliBailianKey);
            
            formData.append('zhongzhan_gpt_key', zhongzhanGptKey);
            formData.append('zhongzhan_gpt_base_url', zhongzhanGptBaseUrl);
            formData.append('zhongzhan_gpt_ocr_model', zhongzhanGptOcrModel);
            formData.append('zhongzhan_claude_key', zhongzhanClaudeKey);
            formData.append('zhongzhan_claude_base_url', zhongzhanClaudeBaseUrl);
            formData.append('zhongzhan_claude_ocr_model', zhongzhanClaudeOcrModel);
            
            formData.append('prefer_engine', preferEngine);
            formData.append('siliconflow_model', siliconflowModel);
            formData.append('ali_bailian_model', aliBailianModel);
            formData.append('prefer_solve_model', preferSolveModel);
            formData.append('prefer_parse_model', preferParseModel);
            formData.append('prefer_classify_model', preferClassifyModel);
            formData.append('prefer_draw_model', preferDrawModel);
            
            // Chain both saves: metadata JSON and ENV settings parameters
            let metaPayload = null;
            const metadataStr = document.getElementById('settingsMetadataJson').value.trim();
            try {
                metaPayload = JSON.parse(metadataStr);
            } catch (err) {
                showToast('元数据配置 JSON 格式错误，请检查括号与逗号！', 'error');
                return;
            }

            fetch('/api/config/metadata', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(metaPayload)
            })
            .then(r => {
                if (!r.ok) {
                    return r.json().then(d => { throw new Error(d.detail || '保存元数据配置失败') });
                }
                return r.json();
            })
            .then(() => {
                return fetch('/api/settings/save', {
                    method: 'POST',
                    body: formData
                });
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast('所有配置（含自定义维度）保存成功！');
                    closeSettingsModal();
                    fetchConfigStatus();
                    loadCategories(); // Reload all dynamic metadata and categories
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                showToast('保存接口设置出错: ' + (err.message || err), 'error');
            });
        }

        // Load Categories from Database to Autocomplete Selects
        function loadCategories(retryCount = 0) {
            fetch('/api/config/metadata')
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`获取元数据状态异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(meta => {
                    systemMetadata = meta;
                    window.systemMetadata = meta; // Global export
                    populateMetadataDropdowns();
                    
                    return fetch('/api/categories');
                })
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(data => {
                    categoryTree = data;
                    window.categoryTree = data;
                    window.systemMetadata = systemMetadata;
                    populateCategoryDropdowns();
                    populateFilterDropdowns();
                    
                    // Reload currently active question silently if one is selected to sync with the new curriculum
                    if (typeof window.reloadCurrentQuestionSilently === 'function') {
                        window.reloadCurrentQuestionSilently();
                    }
                    if (!EditorState.questionId && typeof window.backupEditorState === 'function') {
                        window.backupEditorState(null, null);
                    }
                })
                .catch(err => {
                    console.error('加载分类目录树或元数据配置发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载数据...`);
                        setTimeout(() => loadCategories(retryCount + 1), 1500);
                    } else {
                        showToast('系统正在连接或初始化后台，加载分类及大纲数据失败，请刷新重试', 'error');
                    }
                });
        }

        // Populate Metadata Select Option Lists Dynamically
        function populateMetadataDropdowns() {
            if (!systemMetadata || !systemMetadata.question_types || !systemMetadata.difficulties) return;

            // 1. Edit Question Type select
            const editQType = document.getElementById('editQType');
            if (editQType) {
                const currentVal = editQType.value || 'single_choice';
                editQType.innerHTML = '';
                systemMetadata.question_types.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    editQType.appendChild(opt);
                });
                // Restore selected value if matches
                if (systemMetadata.question_types.some(t => t.value === currentVal)) {
                    editQType.value = currentVal;
                } else if (systemMetadata.question_types.length > 0) {
                    editQType.value = systemMetadata.question_types[0].value;
                }
            }

            // 2. Edit Difficulty select
            const editDiff = document.getElementById('editDifficulty');
            if (editDiff) {
                const currentVal = editDiff.value || 'easy_error';
                editDiff.innerHTML = '';
                systemMetadata.difficulties.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    editDiff.appendChild(opt);
                });
                if (systemMetadata.difficulties.some(d => d.value === currentVal)) {
                    editDiff.value = currentVal;
                } else if (systemMetadata.difficulties.length > 0) {
                    editDiff.value = systemMetadata.difficulties[0].value;
                }
            }

            // 3. Sidebar Filter Question Type select
            const filterQType = document.getElementById('filterQType');
            if (filterQType) {
                const currentVal = filterQType.value || '';
                filterQType.innerHTML = '<option value="">全部题型</option>';
                systemMetadata.question_types.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    filterQType.appendChild(opt);
                });
                filterQType.value = currentVal;
            }

            // 4. Sidebar Filter Difficulty select
            const filterDiff = document.getElementById('filterDifficulty');
            if (filterDiff) {
                const currentVal = filterDiff.value || '';
                filterDiff.innerHTML = '<option value="">全部难度</option>';
                systemMetadata.difficulties.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    filterDiff.appendChild(opt);
                });
                filterDiff.value = currentVal;
            }
        }

        // Settings tab switcher
        let activeSettingsTab = 'api';
        window.switchSettingsTab = function(tabName) {
            activeSettingsTab = tabName;
            const btnApi = document.getElementById('btn-settings-api');
            const btnMeta = document.getElementById('btn-settings-metadata');
            const btnAbout = document.getElementById('btn-settings-about');
            const tabApi = document.getElementById('settings-tab-api');
            const tabMeta = document.getElementById('settings-tab-metadata');
            const tabAbout = document.getElementById('settings-tab-about');
            const btnSave = document.getElementById('btnSettingsSave');
            
            // Reset all buttons
            [btnApi, btnMeta, btnAbout].forEach(b => {
                if (b) {
                    b.classList.remove('border-brand-500', 'text-brand-600');
                    b.classList.add('border-transparent', 'text-slate-500');
                }
            });
            // Hide all tabs
            [tabApi, tabMeta, tabAbout].forEach(t => {
                if (t) t.classList.add('hidden');
            });
            
            if (tabName === 'api') {
                if (btnApi) {
                    btnApi.classList.add('border-brand-500', 'text-brand-600');
                    btnApi.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabApi) tabApi.classList.remove('hidden');
                if (btnSave) btnSave.classList.remove('hidden');
            } else if (tabName === 'metadata') {
                if (btnMeta) {
                    btnMeta.classList.add('border-brand-500', 'text-brand-600');
                    btnMeta.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabMeta) tabMeta.classList.remove('hidden');
                if (btnSave) btnSave.classList.remove('hidden');
                
                // Load latest JSON from API
                fetch('/api/config/metadata')
                    .then(r => r.json())
                    .then(data => {
                        const el = document.getElementById('settingsMetadataJson');
                        if (el) el.value = JSON.stringify(data, null, 2);
                    })
                    .catch(err => {
                        showToast('加载元数据配置失败: ' + err, 'error');
                    });
            } else if (tabName === 'about') {
                if (btnAbout) {
                    btnAbout.classList.add('border-brand-500', 'text-brand-600');
                    btnAbout.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabAbout) tabAbout.classList.remove('hidden');
                if (btnSave) btnSave.classList.add('hidden');
                
                // Refresh update status in About tab
                refreshAboutTabUpdateInfo();
            }
        };

        // Reset Metadata to High school math template
        window.resetMetadataToDefault = function(version = 'A') {
            const versionName = version === 'B' ? '人教B版' : (version === 'S' ? '苏教版' : (version === 'H' ? '沪教版' : '人教A版'));
            if (!confirm(`确认要将所有题型、难度和学段重置为默认的【${versionName}】配置模板吗？这不会修改您的数据库题目，但会替换下方编辑框的内容（需点击保存后生效）。`)) {
                return;
            }

            fetch('/api/config/curriculum-presets/' + encodeURIComponent(version))
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(data => {
                    if (!data || !data.metadata || !data.metadata.curriculum) {
                        throw new Error('教材大纲预设响应格式错误');
                    }
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(data.metadata, null, 2);
                    showToast(`已加载默认【${data.name || versionName}】配置模板，请点击最下方的 [保存配置] 按钮进行保存并应用。`);
                })
                .catch(error => {
                    showToast('加载教材大纲预设失败: ' + error.message, 'error');
                });
        };

        // ----------------- App Version & Update Management -----------------

        let latestReleaseCache = null;
        const IGNORED_UPDATE_KEY = 'mathbank_ignored_update_version';
        const LAST_CHECK_TIME_KEY = 'mathbank_last_update_check_time';

        // Check if user is on Mac or Windows
        function getClientOS() {
            const ua = navigator.userAgent || '';
            if (/macintosh|mac os x/i.test(ua)) return 'macOS';
            if (/windows|win32/i.test(ua)) return 'Windows';
            return 'Other';
        }

        // Open update modal with data
        window.openUpdateModal = function(releaseData) {
            if (!releaseData) return;
            const modal = document.getElementById('updateModal');
            if (!modal) return;

            const titleEl = document.getElementById('updateModalTitle');
            const badgeEl = document.getElementById('updateModalBadge');
            const dateEl = document.getElementById('updateModalDate');
            const bodyEl = document.getElementById('updateModalBody');
            const btnGithub = document.getElementById('btnViewOnGithub');
            const btnIgnoreText = document.getElementById('btnIgnoreVersionText');
            const gitTip = document.getElementById('updateGitTip');

            const latestVer = releaseData.latest_version || releaseData.current_version || 'v2.0.1';
            if (titleEl) titleEl.innerText = `发现新版本 ${latestVer}`;
            if (badgeEl) badgeEl.innerText = releaseData.release_title || 'NEW RELEASE';
            
            if (dateEl) {
                if (releaseData.published_at) {
                    const d = new Date(releaseData.published_at);
                    dateEl.innerText = `发布于 ${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
                } else {
                    dateEl.innerText = '官方正式发布';
                }
            }

            if (bodyEl) {
                bodyEl.innerText = releaseData.release_body || '本版本包含多项性能与体验优化，推荐升级。';
            }

            if (btnGithub && releaseData.release_url) {
                btnGithub.href = releaseData.release_url;
            }

            if (btnIgnoreText) {
                btnIgnoreText.innerText = `忽略此版本 (${latestVer})`;
            }

            if (gitTip) {
                if (releaseData.is_git_repo) {
                    gitTip.classList.remove('hidden');
                } else {
                    gitTip.classList.add('hidden');
                }
            }

            // Bind assets download URLs
            const clientOS = getClientOS();
            const btnMac = document.getElementById('btnDownloadMac');
            const btnWin = document.getElementById('btnDownloadWin');
            const macSize = document.getElementById('macDownloadSize');
            const winSize = document.getElementById('winDownloadSize');

            const macAsset = releaseData.assets && releaseData.assets.macOS;
            const winAsset = releaseData.assets && releaseData.assets.Windows;

            if (btnMac) {
                if (macAsset && macAsset.url) {
                    btnMac.href = macAsset.url;
                    btnMac.classList.remove('opacity-50', 'pointer-events-none');
                    if (macSize) macSize.innerText = `${macAsset.size_mb} MB • .zip 格式`;
                } else {
                    btnMac.href = releaseData.release_url || 'https://github.com/JudgePeach/math-question-bank/releases';
                    if (macSize) macSize.innerText = '前往 Releases 下载';
                }
                if (clientOS === 'macOS') {
                    btnMac.classList.add('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                } else {
                    btnMac.classList.remove('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                }
            }

            if (btnWin) {
                if (winAsset && winAsset.url) {
                    btnWin.href = winAsset.url;
                    btnWin.classList.remove('opacity-50', 'pointer-events-none');
                    if (winSize) winSize.innerText = `${winAsset.size_mb} MB • .zip 格式`;
                } else {
                    btnWin.href = releaseData.release_url || 'https://github.com/JudgePeach/math-question-bank/releases';
                    if (winSize) winSize.innerText = '前往 Releases 下载';
                }
                if (clientOS === 'Windows') {
                    btnWin.classList.add('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                } else {
                    btnWin.classList.remove('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                }
            }

            // Show modal
            modal.classList.remove('hidden');
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                const content = modal.querySelector('.glass-modal');
                if (content) content.classList.remove('scale-95');
            }, 10);
        };

        window.closeUpdateModal = function() {
            const modal = document.getElementById('updateModal');
            if (!modal) return;
            modal.classList.add('opacity-0');
            const content = modal.querySelector('.glass-modal');
            if (content) content.classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        };

        // Ignore current version update
        window.ignoreCurrentVersionUpdate = function() {
            if (latestReleaseCache && latestReleaseCache.latest_version) {
                localStorage.setItem(IGNORED_UPDATE_KEY, latestReleaseCache.latest_version);
                const badge = document.getElementById('updateBadge');
                if (badge) badge.classList.add('hidden');
                showToast(`已忽略版本 ${latestReleaseCache.latest_version} 的更新提醒。后续若有更高版本将再次通知您。`);
            }
            closeUpdateModal();
            refreshAboutTabUpdateInfo();
        };

        // Clear ignored version
        window.clearIgnoredVersion = function() {
            localStorage.removeItem(IGNORED_UPDATE_KEY);
            const badge = document.getElementById('updateBadge');
            if (badge && latestReleaseCache && latestReleaseCache.has_update) {
                badge.classList.remove('hidden');
            }
            showToast('已恢复版本更新提醒。');
            refreshAboutTabUpdateInfo();
        };

        // Toggle ignore current version directly from Settings panel
        window.toggleIgnoreCurrentVersionFromSettings = function() {
            if (!latestReleaseCache || !latestReleaseCache.latest_version) return;
            const currentIgnored = localStorage.getItem(IGNORED_UPDATE_KEY);
            const latestVer = latestReleaseCache.latest_version;
            const badge = document.getElementById('updateBadge');

            if (currentIgnored === latestVer) {
                // Was ignored -> unignore
                localStorage.removeItem(IGNORED_UPDATE_KEY);
                if (badge && latestReleaseCache.has_update) {
                    badge.classList.remove('hidden');
                }
                showToast(`已恢复版本 ${latestVer} 的更新提醒。`);
            } else {
                // Ignore it
                localStorage.setItem(IGNORED_UPDATE_KEY, latestVer);
                if (badge) {
                    badge.classList.add('hidden');
                }
                showToast(`已忽略版本 ${latestVer} 的更新提醒。若后续发布更高版本将再次通知您。`);
            }
            refreshAboutTabUpdateInfo();
        };

        // Refresh update info in About Tab
        window.refreshAboutTabUpdateInfo = function() {
            const textEl = document.getElementById('aboutUpdateStatusText');
            const badgeVer = document.getElementById('aboutCurrentVersionBadge');
            const ignoredRow = document.getElementById('ignoredVersionRow');
            const ignoredText = document.getElementById('ignoredVersionText');
            const ignoredVer = localStorage.getItem(IGNORED_UPDATE_KEY);
            const btnToggleIgnore = document.getElementById('btnToggleIgnoreVersion');
            const iconToggleIgnore = document.getElementById('iconToggleIgnore');
            const textToggleIgnore = document.getElementById('textToggleIgnore');

            if (ignoredVer && ignoredRow && ignoredText) {
                ignoredRow.classList.remove('hidden');
                ignoredText.innerText = `您已忽略版本 ${ignoredVer} 的更新提醒。`;
            } else if (ignoredRow) {
                ignoredRow.classList.add('hidden');
            }

            if (latestReleaseCache) {
                if (badgeVer) badgeVer.innerText = `v${latestReleaseCache.current_version.replace(/^v/i, '')}`;
                if (textEl) {
                    if (latestReleaseCache.has_update) {
                        textEl.innerHTML = `<span class="text-brand-600 font-bold">发现新版本 ${latestReleaseCache.latest_version}</span>（当前为 v${latestReleaseCache.current_version}）`;
                    } else {
                        textEl.innerText = `当前已是最新版本 (v${latestReleaseCache.current_version}) • 运行环境正常`;
                    }
                }

                // Update the quick ignore button on Settings card
                if (latestReleaseCache.has_update && btnToggleIgnore) {
                    btnToggleIgnore.classList.remove('hidden');
                    if (ignoredVer === latestReleaseCache.latest_version) {
                        if (iconToggleIgnore) iconToggleIgnore.className = 'fa-solid fa-bell text-[11px] text-amber-500';
                        if (textToggleIgnore) textToggleIgnore.innerText = '恢复提醒';
                        btnToggleIgnore.title = '恢复该版本的更新提醒与呼吸徽章';
                    } else {
                        if (iconToggleIgnore) iconToggleIgnore.className = 'fa-solid fa-bell-slash text-[11px] text-slate-400';
                        if (textToggleIgnore) textToggleIgnore.innerText = '忽略此版本';
                        btnToggleIgnore.title = '忽略当前版本的更新提醒';
                    }
                } else if (btnToggleIgnore) {
                    btnToggleIgnore.classList.add('hidden');
                }
            }
        };

        // Check update manually
        window.checkAppUpdateManually = async function(isUserTriggered = false) {
            const spinIcon = document.getElementById('checkUpdateSpinIcon');
            const textEl = document.getElementById('aboutUpdateStatusText');
            if (spinIcon) spinIcon.classList.add('fa-spin');
            if (textEl) textEl.innerText = '正在连接 GitHub 检测最新版本...';

            try {
                const res = await fetch('/api/version/check-update');
                const data = await res.json();
                latestReleaseCache = data;
                localStorage.setItem(LAST_CHECK_TIME_KEY, Date.now().toString());

                if (spinIcon) spinIcon.classList.remove('fa-spin');
                refreshAboutTabUpdateInfo();

                if (data.status === 'success') {
                    const ignoredVer = localStorage.getItem(IGNORED_UPDATE_KEY);
                    const badge = document.getElementById('updateBadge');

                    if (data.has_update) {
                        if (badge && (!ignoredVer || ignoredVer !== data.latest_version)) {
                            badge.classList.remove('hidden');
                        }
                        if (isUserTriggered) {
                            openUpdateModal(data);
                        } else if (!ignoredVer || ignoredVer !== data.latest_version) {
                            showToast(`🎉 发现新版本 ${data.latest_version}，可前往设置查看更新内容。`, 'info');
                        }
                    } else {
                        if (badge) badge.classList.add('hidden');
                        if (isUserTriggered) {
                            showToast(`✨ 当前已是最新版本 (v${data.current_version})！`, 'success');
                        }
                    }
                } else if (isUserTriggered) {
                    showToast(data.message || '检查更新失败，请稍后重试', 'warning');
                }
            } catch (err) {
                if (spinIcon) spinIcon.classList.remove('fa-spin');
                if (textEl) textEl.innerText = '检查更新连接超时，请检查网络';
                if (isUserTriggered) {
                    showToast('连接 GitHub 检查更新失败: ' + err.message, 'warning');
                }
            }
        };

        // Developer / user demo preview
        window.previewUpdateModalDemo = function() {
            if (latestReleaseCache) {
                openUpdateModal(latestReleaseCache);
            } else {
                fetch('/api/version/check-update')
                    .then(r => r.json())
                    .then(data => {
                        latestReleaseCache = data;
                        openUpdateModal(data);
                    })
                    .catch(err => {
                        // Fallback demo data
                        const demoData = {
                            current_version: "2.0.1",
                            latest_version: "v2.0.1",
                            release_title: "v2.0.1 正式发布",
                            release_body: "1. 新增 Word (.docx) 试卷 OMML 公式与插图 0 损直提智能拆解。\n2. 全新升级 KaTeX 填空题 \\fillin 宏与数学比较符号防撕裂转义自愈。\n3. 优化拖放区域 UI 与拆分进度动画盒模型。\n4. 内置版本自动检测、忽略更新与便携包一键直链更新体系。",
                            release_url: "https://github.com/JudgePeach/math-question-bank/releases",
                            published_at: new Date().toISOString(),
                            is_git_repo: true,
                            assets: {
                                macOS: { name: "MathBank-macOS.zip", size_mb: 11.7, downloads: 40, url: "https://github.com/JudgePeach/math-question-bank/releases" },
                                Windows: { name: "MathBank-Windows-x64.zip", size_mb: 51.2, downloads: 171, url: "https://github.com/JudgePeach/math-question-bank/releases" }
                            }
                        };
                        openUpdateModal(demoData);
                    });
            }
        };

        // Silent background check on app startup
        window.silentCheckAppUpdateOnStart = function() {
            setTimeout(() => {
                checkAppUpdateManually(false);
            }, 1000);
        };


        // Populate Categories in Editor Selects
        function formatChineseDate(isoStr) {
            if (!isoStr) return '未知时间';
            try {
                const date = new Date(isoStr);
                if (isNaN(date.getTime())) return '未知时间';
                const y = date.getFullYear();
                const m = String(date.getMonth() + 1).padStart(2, '0');
                const d = String(date.getDate()).padStart(2, '0');
                const h = String(date.getHours()).padStart(2, '0');
                const min = String(date.getMinutes()).padStart(2, '0');
                return `${y}年${m}月${d}日 ${h}时${min}分`;
            } catch (e) {
                return '未知时间';
            }
        }

        // Helper string mappings
        function getDifficultyColor(val) {
            if (!val) return 'text-slate-550 bg-slate-100 border border-slate-200/60';
            if (systemMetadata && Array.isArray(systemMetadata.difficulties) && systemMetadata.difficulties.length > 0) {
                const found = systemMetadata.difficulties.find(d => d.value === val);
                if (found && found.color) {
                    return found.color;
                }
            }
            if (val === 'easy_error') return 'text-green-600 bg-green-50 border border-green-200/60';
            if (val === 'normal') return 'text-blue-600 bg-blue-50 border border-blue-200/60';
            if (val === 'challenge') return 'text-red-600 bg-red-50 border border-red-200/60';
            if (val === 'qiangji') return 'text-purple-600 bg-purple-50 border border-purple-200/60';
            return 'text-slate-550 bg-slate-100 border border-slate-200/60';
        }
        window.getDifficultyColor = getDifficultyColor;

        function getDifficultyBadge(diff) {
            if (!systemMetadata || !systemMetadata.difficulties || systemMetadata.difficulties.length === 0) {
                if (diff === 'easy_error') return '<span class="text-[9px] font-bold text-green-600 bg-green-50 px-1.5 py-0.5 rounded">易错</span>';
                if (diff === 'normal') return '<span class="text-[9px] font-bold text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded">常规</span>';
                if (diff === 'challenge') return '<span class="text-[9px] font-bold text-red-600 bg-red-50 px-1.5 py-0.5 rounded">挑战</span>';
                if (diff === 'qiangji') return '<span class="text-[9px] font-bold text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded">强基</span>';
                return '<span class="text-[9px] font-bold text-slate-550 bg-slate-100 px-1.5 py-0.5 rounded">未定</span>';
            }
            const found = systemMetadata.difficulties.find(d => d.value === diff);
            if (found) {
                // Strip emojis (symbols) from label to keep badge clean and neat
                const cleanLabel = found.label.replace(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD00-\uDFFF]/g, '').trim();
                return `<span class="text-[9px] font-bold ${found.color || 'text-slate-550 bg-slate-100'} px-1.5 py-0.5 rounded">${cleanLabel}</span>`;
            }
            return `<span class="text-[9px] font-bold text-slate-550 bg-slate-100 px-1.5 py-0.5 rounded">${diff}</span>`;
        }

        function getTypeText(type) {
            if (!systemMetadata || !systemMetadata.question_types || systemMetadata.question_types.length === 0) {
                if (type === 'single_choice') return '单选题';
                if (type === 'multi_choice') return '多选题';
                if (type === 'fill_in_blank') return '填空题';
                if (type === 'detailed_answer') return '解答题';
                return '数学题';
            }
            const found = systemMetadata.question_types.find(t => t.value === type);
            return found ? found.label : type;
        }

        function getDifficultyText(val) {
            if (!systemMetadata || !systemMetadata.difficulties || systemMetadata.difficulties.length === 0) {
                if (val === 'easy_error') return '易错题';
                if (val === 'normal') return '常规题';
                if (val === 'challenge') return '挑战题';
                if (val === 'qiangji') return '强基题';
                return '未定';
            }
            const found = systemMetadata.difficulties.find(d => d.value === val);
            return found ? found.label : val;
        }

        // Tab Switching for 3-in-1 workflow
        function escapeRegExp(string) {
            return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        // Global Theme Color and Dark Mode Management
        window.changeTheme = function(themeName, save = true) {
            const themes = ['theme-obsidian', 'theme-violet', 'theme-emerald', 'theme-ocean', 'theme-amber', 'theme-crimson'];
            
            // Remove all themes from document root and body
            themes.forEach(t => {
                document.documentElement.classList.remove(t);
                document.body.classList.remove(t);
            });
            
            // Apply selected theme
            const newThemeClass = `theme-${themeName}`;
            document.documentElement.classList.add(newThemeClass);
            document.body.classList.add(newThemeClass);

            // Update dropdown UI (Dot, Text, Checkmarks)
            const dot = document.getElementById('currentThemeDot');
            const nameSpan = document.getElementById('currentThemeName');
            if (dot && nameSpan) {
                const colorMap = {
                    'obsidian': '#0f172a',
                    'violet': '#8b5cf6',
                    'ocean': '#0ea5e9',
                    'emerald': '#10b981',
                    'amber': '#f59e0b',
                    'crimson': '#f43f5e'
                };
                dot.style.backgroundColor = colorMap[themeName] || '#0f172a';
                nameSpan.textContent = getThemeChineseName(themeName);
            }
            
            // Update check marks
            const themesOnly = ['obsidian', 'violet', 'ocean', 'emerald', 'amber', 'crimson'];
            themesOnly.forEach(t => {
                const check = document.getElementById(`check-${t}`);
                if (check) {
                    if (t === themeName) {
                        check.classList.remove('hidden');
                    } else {
                        check.classList.add('hidden');
                    }
                }
            });
            
            if (save) {
                localStorage.setItem('theme-color', themeName);
                if (typeof showToast === 'function') {
                    showToast(`已切换至 ${getThemeChineseName(themeName)} 配色`);
                }
            }
        };

        // Dropdown toggle logic
        window.toggleThemeDropdown = function(event) {
            event.stopPropagation();
            const menu = document.getElementById('themeDropdownMenu');
            const arrow = document.getElementById('themeDropdownArrow');
            if (!menu) return;
            
            const isOpen = !menu.classList.contains('pointer-events-none');
            if (isOpen) {
                closeThemeDropdown();
            } else {
                menu.classList.remove('scale-90', 'opacity-0', 'pointer-events-none');
                menu.classList.add('scale-100', 'opacity-100');
                if (arrow) arrow.classList.add('rotate-180');
                
                // Add click listener to close when clicking outside
                document.addEventListener('click', closeThemeDropdownOnce);
            }
        };
        
        function closeThemeDropdown() {
            const menu = document.getElementById('themeDropdownMenu');
            const arrow = document.getElementById('themeDropdownArrow');
            if (!menu) return;
            menu.classList.add('scale-90', 'opacity-0', 'pointer-events-none');
            menu.classList.remove('scale-100', 'opacity-100');
            if (arrow) arrow.classList.remove('rotate-180');
            document.removeEventListener('click', closeThemeDropdownOnce);
        }
        
        function closeThemeDropdownOnce() {
            closeThemeDropdown();
        }

        window.selectTheme = function(themeName) {
            window.changeTheme(themeName);
            closeThemeDropdown();
        };

        // Workspace Dropdown toggle logic
        window.toggleWorkspaceDropdown = function(event) {
            event.stopPropagation();
            const menu = document.getElementById('workspaceDropdownMenu');
            const arrow = document.getElementById('workspaceDropdownArrow');
            if (!menu) return;
            
            const isOpen = !menu.classList.contains('pointer-events-none');
            if (isOpen) {
                closeWorkspaceDropdown();
            } else {
                if (typeof closeThemeDropdown === 'function') closeThemeDropdown();
                menu.classList.remove('scale-90', 'opacity-0', 'pointer-events-none');
                menu.classList.add('scale-100', 'opacity-100');
                if (arrow) arrow.classList.add('rotate-180');
                document.addEventListener('click', closeWorkspaceDropdownOnce);
            }
        };

        function closeWorkspaceDropdown() {
            const menu = document.getElementById('workspaceDropdownMenu');
            const arrow = document.getElementById('workspaceDropdownArrow');
            if (!menu) return;
            menu.classList.add('scale-90', 'opacity-0', 'pointer-events-none');
            menu.classList.remove('scale-100', 'opacity-100');
            if (arrow) arrow.classList.remove('rotate-180');
            document.removeEventListener('click', closeWorkspaceDropdownOnce);
        }

        function closeWorkspaceDropdownOnce() {
            closeWorkspaceDropdown();
        }

        window.selectWorkspace = function(workspaceId, workspaceName) {
            const currentWorkspaceName = document.getElementById('currentWorkspaceName');
            if (currentWorkspaceName) {
                currentWorkspaceName.textContent = workspaceName;
            }

            const toggleSidebarBtn = document.getElementById('toggleSidebarBtn');
            if (toggleSidebarBtn) {
                if (workspaceId === 'paper') {
                    toggleSidebarBtn.classList.add('hidden');
                } else {
                    toggleSidebarBtn.classList.remove('hidden');
                }
            }

            const checkBank = document.getElementById('ws-check-bank');
            const checkPaper = document.getElementById('ws-check-paper');
            const btnBank = document.getElementById('ws-btn-bank');
            const btnPaper = document.getElementById('ws-btn-paper');

            if (checkBank && checkPaper) {
                if (workspaceId === 'bank') {
                    checkBank.classList.remove('hidden');
                    checkPaper.classList.add('hidden');
                    if (btnBank) btnBank.classList.add('font-medium');
                    if (btnPaper) btnPaper.classList.remove('font-medium');
                } else {
                    checkBank.classList.add('hidden');
                    checkPaper.classList.remove('hidden');
                    if (btnBank) btnBank.classList.remove('font-medium');
                    if (btnPaper) btnPaper.classList.add('font-medium');
                }
            }

            closeWorkspaceDropdown();
        };

        let isSidebarCollapsed = false;
        let lastSidebarWidth = 320;
        let lastPreviewWidth = 450;

        window.toggleSidebarCollapse = function() {
            const sidebar = document.getElementById('sidebarSection');
            const preview = document.getElementById('previewSection');
            const resizer = document.getElementById('resizer-1');
            const toggleBtn = document.getElementById('toggleSidebarBtn');
            if (!sidebar) return;
            
            if (!isSidebarCollapsed) {
                // Collapse
                const currentSidebarWidth = parseFloat(getComputedStyle(sidebar).width) || 320;
                if (currentSidebarWidth > 50) lastSidebarWidth = currentSidebarWidth;
                
                if (preview) {
                    const currentPreviewWidth = parseFloat(getComputedStyle(preview).width) || 450;
                    if (currentPreviewWidth > 100) lastPreviewWidth = currentPreviewWidth;
                }

                sidebar.style.transition = 'width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease';
                sidebar.style.width = '0px';
                sidebar.style.minWidth = '0px';
                sidebar.style.opacity = '0';
                sidebar.style.pointerEvents = 'none';
                if (resizer) resizer.style.display = 'none';
                if (toggleBtn) {
                    toggleBtn.classList.add('text-brand-600', 'bg-slate-100/80');
                    toggleBtn.setAttribute('title', '展开左侧题库栏');
                }
                const toggleIcon = document.getElementById('toggleSidebarIcon');
                if (toggleIcon) {
                    toggleIcon.classList.remove('fa-angles-left');
                    toggleIcon.classList.add('fa-angles-right');
                }

                // Expand preview panel proportionally to 40% window width (60:40 ratio with editor)
                if (preview) {
                    const windowWidth = window.innerWidth || document.documentElement.clientWidth || 1400;
                    const targetPreviewWidth = Math.min(Math.max(Math.round(windowWidth * 0.40), 480), 750);
                    preview.style.transition = 'width 0.25s cubic-bezier(0.4, 0, 0.2, 1)';
                    preview.style.width = targetPreviewWidth + 'px';
                }

                isSidebarCollapsed = true;
                setTimeout(() => {
                    if (preview && isSidebarCollapsed) preview.style.transition = '';
                }, 260);
            } else {
                // Expand
                sidebar.style.transition = 'width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease';
                sidebar.style.width = `${lastSidebarWidth}px`;
                sidebar.style.minWidth = '';
                sidebar.style.opacity = '1';
                sidebar.style.pointerEvents = 'auto';
                if (resizer) resizer.style.display = 'block';
                if (toggleBtn) {
                    toggleBtn.classList.remove('text-brand-600', 'bg-slate-100/80');
                    toggleBtn.setAttribute('title', '收起左侧题库栏');
                }
                const toggleIcon = document.getElementById('toggleSidebarIcon');
                if (toggleIcon) {
                    toggleIcon.classList.remove('fa-angles-right');
                    toggleIcon.classList.add('fa-angles-left');
                }

                // Restore preview panel to previous width
                if (preview) {
                    preview.style.transition = 'width 0.25s cubic-bezier(0.4, 0, 0.2, 1)';
                    preview.style.width = `${lastPreviewWidth}px`;
                }

                isSidebarCollapsed = false;
                setTimeout(() => {
                    if (!isSidebarCollapsed) {
                        sidebar.style.transition = '';
                        if (preview) preview.style.transition = '';
                    }
                }, 260);
            }
        };

        window.toggleDarkMode = function() {
            const isDark = document.documentElement.classList.toggle('dark');
            document.body.classList.toggle('dark', isDark);
            
            localStorage.setItem('dark-mode', isDark);
            updateDarkModeIcon(isDark);
            
            if (typeof showToast === 'function') {
                showToast(isDark ? '已开启优雅夜间模式 🌙' : '已返回亮色模式 ☀️');
            }
        };

        function getThemeChineseName(themeName) {
            const names = {
                'obsidian': '曜石黑',
                'violet': '罗兰紫',
                'emerald': '翡翠绿',
                'ocean': '深海蓝',
                'amber': '琥珀橙',
                'crimson': '玫瑰红'
            };
            return names[themeName] || themeName;
        }

        function updateDarkModeIcon(isDark) {
            const btn = document.getElementById('darkModeBtn');
            if (btn) {
                btn.innerHTML = isDark 
                    ? '<i class="fa-solid fa-sun text-xs text-amber-500"></i>' 
                    : '<i class="fa-solid fa-moon text-xs"></i>';
                btn.title = isDark ? '切换亮色模式' : '切换夜间模式';
            }
        }

        function initTheme() {
            const savedTheme = localStorage.getItem('theme-color') || 'violet';
            const savedDarkMode = localStorage.getItem('dark-mode') === 'true';
            
            // Apply saved theme color without triggering toast
            window.changeTheme(savedTheme, false);
            
            // Apply saved dark mode status
            if (savedDarkMode) {
                document.documentElement.classList.add('dark');
                document.body.classList.add('dark');
                updateDarkModeIcon(true);
            } else {
                document.documentElement.classList.remove('dark');
                document.body.classList.remove('dark');
                updateDarkModeIcon(false);
            }
        }

        // Initialize theme and check update on DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {
            initTheme();
            if (window.silentCheckAppUpdateOnStart) {
                window.silentCheckAppUpdateOnStart();
            }
        });

        /**
         * =========================================================================
         * 全局极速自定义 Tooltip 悬浮提示系统 (Global Fast Tooltip System)
         * =========================================================================
         * 自动接管全局所有带有 title 与 data-tooltip 属性的按钮与元素：
         * 1. 移入按钮稳定停顿 0.5 秒 (500ms) 后平滑浮现提示文字。
         * 2. 移出按钮瞬间 0ms 立即消失隐藏，杜绝旧按钮文字滞留与残影。
         * 3. 滑入新按钮重新独立计时 0.5 秒，鼠标快速扫过不闪烁。
         * 4. 自动移除原生 title 属性暂存至 dataset.tooltip，防止 2 秒后原生浏览器气泡重叠打架。
         * 5. 智能自适应视口边缘碰撞与上下翻转，点击、滚动与按键时即刻淡出隐藏。
         */
        (function initGlobalFastTooltip() {
            let tooltipEl = null;
            let showTimer = null;
            let activeTarget = null;
            const SHOW_DELAY = 500; // 首次悬停停顿 0.5 秒 (500ms) 后显示
            const OFFSET_Y = 6;     // 与目标元素的垂直微间距

            function ensureTooltipElement() {
                if (!tooltipEl) {
                    tooltipEl = document.createElement('div');
                    tooltipEl.className = 'global-tooltip';
                    tooltipEl.setAttribute('role', 'tooltip');
                    tooltipEl.setAttribute('aria-hidden', 'true');
                    document.body.appendChild(tooltipEl);
                }
                return tooltipEl;
            }

            function getTooltipText(element) {
                if (!element || element.nodeType !== 1) return '';
                // 1. 优先读取已缓存或声明的 data-tooltip
                if (element.dataset && element.dataset.tooltip) {
                    return element.dataset.tooltip.trim();
                }
                // 2. 自动捕获原生 title 并抹除原生属性以防原生 2s 延迟黑框弹出
                if (element.hasAttribute('title')) {
                    const rawTitle = element.getAttribute('title') || '';
                    if (rawTitle.trim()) {
                        element.dataset.tooltip = rawTitle.trim();
                        element.removeAttribute('title');
                        return rawTitle.trim();
                    }
                }
                return '';
            }

            function positionTooltip(target, el) {
                const rect = target.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;

                // 强制测量当前 tooltip 尺寸
                el.style.left = '0px';
                el.style.top = '0px';
                const tipWidth = el.offsetWidth || 100;
                const tipHeight = el.offsetHeight || 28;
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;

                // 水平居中计算并进行安全边缘 Clamp (左右保留 8px 安全边距)
                let left = rect.left + (rect.width - tipWidth) / 2;
                left = Math.max(8, Math.min(left, viewportWidth - tipWidth - 8));

                // 垂直定位计算：优先放置在下方，若下方空间不足则放置在上方
                let top = rect.bottom + OFFSET_Y;
                if (top + tipHeight > viewportHeight - 8) {
                    top = rect.top - tipHeight - OFFSET_Y;
                }
                if (top < 8) {
                    top = 8;
                }

                el.style.left = Math.round(left) + 'px';
                el.style.top = Math.round(top) + 'px';
            }

            function hideImmediately() {
                if (showTimer) {
                    clearTimeout(showTimer);
                    showTimer = null;
                }
                if (tooltipEl) {
                    tooltipEl.classList.remove('is-visible');
                    tooltipEl.setAttribute('aria-hidden', 'true');
                }
                activeTarget = null;
            }

            function scheduleShow(target, text) {
                hideImmediately();
                activeTarget = target;
                showTimer = setTimeout(() => {
                    if (!target || !target.isConnected || activeTarget !== target) return;
                    const el = ensureTooltipElement();
                    el.textContent = text;
                    positionTooltip(target, el);
                    el.classList.add('is-visible');
                    el.setAttribute('aria-hidden', 'false');
                }, SHOW_DELAY);
            }

            // 全局捕获 mouseover 事件代理
            document.addEventListener('mouseover', (e) => {
                const target = e.target && e.target.closest ? e.target.closest('[title], [data-tooltip]') : null;
                if (!target) {
                    if (activeTarget && !e.target.closest('.global-tooltip')) {
                        hideImmediately();
                    }
                    return;
                }

                const text = getTooltipText(target);
                if (!text) {
                    hideImmediately();
                    return;
                }

                if (activeTarget === target) {
                    return;
                }

                scheduleShow(target, text);
            }, true);

            // 鼠标移出目标：立即瞬间隐藏，绝不滞留
            document.addEventListener('mouseout', (e) => {
                const target = e.target && e.target.closest ? e.target.closest('[title], [data-tooltip]') : null;
                if (target && target === activeTarget) {
                    if (!e.relatedTarget || !target.contains(e.relatedTarget)) {
                        hideImmediately();
                    }
                }
            }, true);

            // 点击、触控、滚动或按键时瞬间隐藏，绝不遮挡操作
            ['mousedown', 'pointerdown', 'touchstart', 'wheel', 'scroll'].forEach((evtName) => {
                window.addEventListener(evtName, hideImmediately, { passive: true, capture: true });
            });

            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') hideImmediately();
            });

            window.addEventListener('blur', hideImmediately);
        })();
