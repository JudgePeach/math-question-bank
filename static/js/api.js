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

        // Global variables
        let currentQuestionId = null;
        let currentSeqNum = null;
        let currentDraftId = null; // Track if current editing item is a draft
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
                        if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3.5-4B';
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
                "deepseek-ai/DeepSeek-V4-Flash",
                "Qwen/Qwen3.5-4B"
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
                // 中转站模式：采用 input + datalist 实现“可写、可点选历史记录”的高效设计
                const datalistId = `datalist_${typeKey}_${provider}`;
                const models = getModelListForProvider(provider, typeKey);
                let optionsHtml = models.map(m => `<option value="${m}">`).join('');
                
                container.innerHTML = `
                    <input type="text" id="settings_${typeKey}_model_input" list="${datalistId}" 
                           placeholder="手写输入模型名称，或点击右侧 [+] 按钮记录"
                           value="${selectedValue}" class="glass-input flex-1 px-2.5 py-1.5 rounded-lg text-xs font-mono">
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
                    if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3.5-4B';
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
                    return input ? input.value.trim() : '';
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
                    populateCategoryDropdowns();
                    populateFilterDropdowns();
                    
                    // Reload currently active question silently if one is selected to sync with the new curriculum
                    if (typeof window.reloadCurrentQuestionSilently === 'function') {
                        window.reloadCurrentQuestionSilently();
                    }
                    if (!currentQuestionId && typeof window.backupEditorState === 'function') {
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
            const tabApi = document.getElementById('settings-tab-api');
            const tabMeta = document.getElementById('settings-tab-metadata');
            
            if (tabName === 'api') {
                btnApi.classList.add('border-brand-500', 'text-brand-600');
                btnApi.classList.remove('border-transparent', 'text-slate-500');
                btnMeta.classList.add('border-transparent', 'text-slate-500');
                btnMeta.classList.remove('border-brand-500', 'text-brand-600');
                
                tabApi.classList.remove('hidden');
                tabMeta.classList.add('hidden');
            } else {
                btnMeta.classList.add('border-brand-500', 'text-brand-600');
                btnMeta.classList.remove('border-transparent', 'text-slate-500');
                btnApi.classList.add('border-transparent', 'text-slate-500');
                btnApi.classList.remove('border-brand-500', 'text-brand-600');
                
                tabMeta.classList.remove('hidden');
                tabApi.classList.add('hidden');
                
                // Load latest JSON from API
                fetch('/api/config/metadata')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('settingsMetadataJson').value = JSON.stringify(data, null, 2);
                    })
                    .catch(err => {
                        showToast('加载元数据配置失败: ' + err, 'error');
                    });
            }
        };

        // Reset Metadata to High school math template
        window.resetMetadataToDefault = function(version = 'A') {
            const versionName = version === 'B' ? '人教B版' : (version === 'S' ? '苏教版' : '人教A版');
            if (confirm(`确认要将所有题型、难度和学段重置为默认的【${versionName}】配置模板吗？这不会修改您的数据库题目，但会替换下方编辑框的内容（需点击保存后生效）。`)) {
                
                const curriculumA = {
                    "必修一": {
                        "1. 集合与常用逻辑用语": ["1.1 集合的概念", "1.2 集合间的基本关系", "1.3 集合的基本运算", "1.4 充分条件与必要条件", "1.5 全称量词与存在量词"],
                        "2. 一元二次函数、方程和不等式": ["2.1 等式性质与不等式性质", "2.2 基本不等式", "2.3 二次函数与一元二次方程、不等式"],
                        "3. 函数的概念与性质": ["3.1 函数的概念及其表示", "3.2 函数的基本性质", "3.3 幂函数", "3.4 函数的应用(一)"],
                        "4. 指数函数与对数函数": ["4.1 指数", "4.2 指数函数", "4.3 对数", "4.4 对数函数", "4.5 函数的应用(二)"],
                        "5. 三角函数": ["5.1 任意角和弧度制", "5.2 三角函数的概念", "5.3 诱导公式", "5.4 三角函数的图象与性质", "5.5 三角恒等变换", "5.6 函数y=Asin(wx+φ)", "5.7 三角函数的应用"]
                    },
                    "必修二": {
                        "6. 平面向量及其应用": ["6.1 平面向量的概念", "6.2 平面向量的运算", "6.3 平面向量基本定理及坐标表示", "6.4 平面向量的应用"],
                        "7. 复数": ["7.1 复数的概念", "7.2 复数的四则运算", "7.3 复数的三角表示"],
                        "8. 立体几何初步": ["8.1 基本立体图形", "8.2 立体图形的直观图", "8.3 简单几何体的表面积与体积", "8.4 空间点、直线、平面之间的位置关系", "8.5 空间直线、平面的平行", "8.6 空间直线、平面的垂直"],
                        "9. 统计": ["9.1 随机抽样", "9.2 用样本估计总体", "9.3 统计案例"],
                        "10. 概率": ["10.1 随机事件与概率", "10.2 事件的相互独立性", "10.3 频率与概率"]
                    },
                    "选修一": {
                        "1. 空间向量与立体几何": ["1.1 空间向量及其运算", "1.2 空间向量基本定理", "1.3 空间向量及其运算的坐标表示", "1.4 空间向量的应用"],
                        "2. 直线和圆的方程": ["2.1 直线的倾斜角和斜率", "2.2 直线的方程", "2.3 直线的交点坐标与距离公式", "2.4 圆的方程", "2.5 直线与圆、圆与圆的位置关系"],
                        "3. 圆锥曲线的方程": ["3.1 椭圆", "3.2 双曲线", "3.3 抛物线"]
                    },
                    "选修二": {
                        "4. 数列": ["4.1 数列的概念", "4.2 等差数列", "4.3 等比数列", "4.4 数学归纳法"],
                        "5. 一元函数的导数及其应用": ["5.1 导数的概念及其意义", "5.2 导数的运算", "5.3 导数在研究函数中的应用"]
                    },
                    "选修三": {
                        "6. 计数原理": ["6.1 分类加法计数原理与分步乘法计数原理", "6.2 排列与组合", "6.3 二项式定理"],
                        "7. 随机变量及其分布": ["7.1 条件概率与全概率公式", "7.2 离散型随机变量及其分布列", "7.3 离散型随机变量的数字特征", "7.4 二项分布与超几何分布", "7.5 正态分布"],
                        "8. 成对数据的统计分析": ["8.1 成对数据的统计相关性", "8.2 一元线性回归模型及其应用", "8.3 列联表与独立性检验"]
                    }
                };

                const curriculumB = {
                    "必修一": {
                        "第一章 集合与常用逻辑用语": [
                            "1.1 集合",
                            "1.1.1 集合及其表示方法",
                            "1.1.2 集合的基本关系",
                            "1.1.3 集合的基本运算",
                            "1.2 常用逻辑用语",
                            "1.2.1 命题与量词",
                            "1.2.2 全称量词命题与存在量词命题的否定",
                            "1.2.3 充分条件、必要条件"
                        ],
                        "第二章 等式与不等式": [
                            "2.1 等式",
                            "2.1.1 等式的性质与方程的解集",
                            "2.1.2 一元二次方程的解集及其根与系数的关系",
                            "2.1.3 方程组的解集",
                            "2.2 不等式",
                            "2.2.1 不等式及其性质",
                            "2.2.2 不等式的解集",
                            "2.2.3 一元二次不等式的解法",
                            "2.2.4 均值不等式及其应用"
                        ],
                        "第三章 函数": [
                            "3.1 函数的概念与性质",
                            "3.1.1 函数及其表示方法",
                            "3.1.2 函数的单调性",
                            "3.1.3 函数的奇偶性",
                            "3.2 函数与方程、不等式之间的关系",
                            "3.3 函数的应用（一）"
                        ]
                    },
                    "必修二": {
                        "第四章 指数函数、对数函数与幂函数": [
                            "4.1 指数与指数函数",
                            "4.1.1 实数指数幂及其运算",
                            "4.1.2 指数函数的性质与图象",
                            "4.2 对数与对数函数",
                            "4.2.1 对数运算",
                            "4.2.2 对数运算法则",
                            "4.2.3 对数函数的性质与图象",
                            "4.3 指数函数与对数函数的关系",
                            "4.4 幂函数",
                            "4.5 增长速度的比较",
                            "4.6 函数的应用（二）"
                        ],
                        "第五章 统计与概率": [
                            "5.1 统计",
                            "5.1.1 数据的收集",
                            "5.1.2 数据的数字特征",
                            "5.1.3 数据的直观表示",
                            "5.1.4 用样本估计总体",
                            "5.3 概率",
                            "5.3.1 样本空间与事件",
                            "5.3.2 事件之间的关系与运算",
                            "5.3.3 古典概型",
                            "5.3.4 频率与概率",
                            "5.3.5 随机事件的独立性",
                            "5.4 统计与概率的应用"
                        ],
                        "第六章 平面向量初步": [
                            "6.1 平面向量及其线性运算",
                            "6.1.1 向量的概念",
                            "6.1.2 向量的加法",
                            "6.1.3 向量的减法",
                            "6.1.4 数乘向量",
                            "6.1.5 向量的线性运算",
                            "6.2 向量基本定理与向量的坐标",
                            "6.2.1 向量基本定理",
                            "6.2.2 直线上向量的坐标及其运算",
                            "6.2.3 平面向量的坐标及其运算",
                            "6.3 平面向量线性运算的应用"
                        ]
                    },
                    "必修三": {
                        "第七章 三角函数": [
                            "7.1 任意角的概念与弧度制",
                            "7.1.1 角的推广",
                            "7.1.2 弧度制及其与角度制的换算",
                            "7.2 任意角的三角函数",
                            "7.2.1 三角函数的定义",
                            "7.2.2 单位圆与三角函数线",
                            "7.2.3 同角三角函数的基本关系式",
                            "7.2.4 诱导公式",
                            "7.3 三角函数的性质与图象",
                            "7.3.1 正弦函数的性质与图象",
                            "7.3.2 正弦型函数的性质与图象",
                            "7.3.3 余弦函数的性质与图象",
                            "7.3.4 正切函数的性质与图象",
                            "7.3.5 已知三角函数值求角",
                            "7.4 数学建模活动：周期现象的描述"
                        ],
                        "第八章 向量的数量积与三角恒等变换": [
                            "8.1 向量的数量积",
                            "8.1.1 向量数量积的概念",
                            "8.1.2 向量数量积的运算律",
                            "8.1.3 向量数量积的坐标运算",
                            "8.2 三角恒等变换",
                            "8.2.1 两角和与差的余弦",
                            "8.2.2 两角和与差的正弦、正切",
                            "8.2.3 倍角公式",
                            "8.2.4 三角恒等变换的应用"
                        ]
                    },
                    "必修四": {
                        "第九章 解三角形": [
                            "9.1 正弦定理与余弦定理",
                            "9.1.1 正弦定理",
                            "9.1.2 余弦定理",
                            "9.2 正弦定理与余弦定理的应用"
                        ],
                        "第十章 复数": [
                            "10.1 复数及其几何意义",
                            "10.1.1 复数的概念",
                            "10.1.2 复数的几何意义",
                            "10.2 复数的运算",
                            "10.2.1 复数的加法与减法",
                            "10.2.2 复数的乘法与除法",
                            "10.3 复数的三角形式及其运算"
                        ],
                        "第十一章 立体几何初步": [
                            "11.1 空间几何体",
                            "11.1.1 空间几何体与斜二测画法",
                            "11.1.2 构成空间几何体的基本元素",
                            "11.1.3 多面体与棱柱",
                            "11.1.4 棱锥与棱台",
                            "11.1.5 旋转体",
                            "11.1.6 祖暅原理与几何体的体积",
                            "11.2 平面的基本事实与推论",
                            "11.3 空间中的平行关系",
                            "11.3.1 平行直线与异面直线",
                            "11.3.2 直线与平面平行",
                            "11.3.3 平面与平面平行",
                            "11.4 空间中的垂直关系",
                            "11.4.1 直线与平面垂直",
                            "11.4.2 平面与平面垂直"
                        ]
                    },
                    "选修一": {
                        "第一章 空间向量与立体几何": [
                            "1.1 空间向量及其运算",
                            "1.1.1 空间向量及其运算",
                            "1.1.2 空间向量基本定理",
                            "1.1.3 空间向量的坐标与空间直角坐标系",
                            "1.2 空间向量在立体几何中的应用",
                            "1.2.1 空间中的点、直线与空间向量",
                            "1.2.2 空间中的平面与空间向量",
                            "1.2.3 直线与平面的夹角",
                            "1.2.4 二面角",
                            "1.2.5 空间中的距离"
                        ],
                        "第二章 平面解析几何": [
                            "2.1 坐标法",
                            "2.2 直线及其方程",
                            "2.2.1 直线的倾斜角与斜率",
                            "2.2.2 直线的方程",
                            "2.2.3 两条直线的位置关系",
                            "2.2.4 点到直线的距离",
                            "2.3 圆及其方程",
                            "2.3.1 圆的标准方程",
                            "2.3.2 圆的一般方程",
                            "2.3.3 直线与圆的位置关系",
                            "2.3.4 圆与圆的位置关系",
                            "2.4 曲线与方程",
                            "2.5 椭圆及其方程",
                            "2.5.1 椭圆的标准方程",
                            "2.5.2 椭圆的几何性质",
                            "2.6 双曲线及其方程",
                            "2.6.1 双曲线的标准方程",
                            "2.6.2 双曲线的几何性质",
                            "2.7 抛物线及其方程",
                            "2.7.1 抛物线的标准方程",
                            "2.7.2 抛物线的几何性质",
                            "2.8 直线与圆锥曲线的位置关系"
                        ]
                    },
                    "选修二": {
                        "第三章 排列、组合与二项式定理": [
                            "3.1 排列与组合",
                            "3.1.1 基本计数原理",
                            "3.1.2 排列与排列数",
                            "3.1.3 组合与组合数",
                            "3.3 二项式定理与杨辉三角"
                        ],
                        "第四章 概率与统计": [
                            "4.1 条件概率与事件的独立性",
                            "4.1.1 条件概率",
                            "4.1.2 乘法公式与全概率公式",
                            "4.1.3 独立性与条件概率的关系",
                            "4.2 随机变量",
                            "4.2.1 随机变量及其与事件的联系",
                            "4.2.2 离散型随机变量的分布列",
                            "4.2.3 二项分布与超几何分布",
                            "4.2.4 随机变量的数字特征",
                            "4.2.5 正态分布",
                            "4.3 统计模型",
                            "4.3.1 一元线性回归模型",
                            "4.3.2 独立性检验"
                        ]
                    },
                    "选修三": {
                        "第五章 数列": [
                            "5.1 数列基础",
                            "5.1.1 数列的概念",
                            "5.1.2 数列中的递推",
                            "5.2 等差数列",
                            "5.2.1 等差数列",
                            "5.2.2 等差数列的前n项和",
                            "5.3 等比数列",
                            "5.3.1 等比数列",
                            "5.3.2 等比数列的前n项和",
                            "5.4 数列的应用",
                            "5.5 数学归纳法"
                        ],
                        "第六章 导数及其应用": [
                            "6.1 导数",
                            "6.1.1 函数的平均变化率",
                            "6.1.2 导数及其几何意义",
                            "6.1.3 基本初等函数的导数",
                            "6.1.4 求导法则及其应用",
                            "6.2 利用导数研究函数的性质",
                            "6.2.1 导数与函数的单调性",
                            "6.2.2 导数与函数的极值、最值",
                            "6.3 利用导数解决实际问题",
                            "6.4 数学建模活动：描述体重与脉搏率的关系"
                        ]
                    }
                };

                const curriculumS = {
                    "必修一": {
                        "第1章 集合": [
                            "1.1 集合的概念与表示",
                            "1.2 子集、全集、补集",
                            "1.3 交集、并集"
                        ],
                        "第2章 常用逻辑用语": [
                            "2.1 命题、定理、定义",
                            "2.2 充分条件、必要条件、充要条件",
                            "2.3 全称量词命题与存在量词命题"
                        ],
                        "第3章 不等式": [
                            "3.1 不等式的基本性质",
                            "3.2 基本不等式",
                            "3.3 从函数观点看一元二次方程和一元二次不等式"
                        ],
                        "第4章 指数与对数": [
                            "4.1 指数",
                            "4.2 对数"
                        ],
                        "第5章 函数概念与性质": [
                            "5.1 函数的概念和图象",
                            "5.2 函数的表示方法",
                            "5.3 函数的单调性",
                            "5.4 函数的奇偶性"
                        ],
                        "第6章 幂函数、指数函数和对数函数": [
                            "6.1 幂函数",
                            "6.2 指数函数",
                            "6.3 对数函数"
                        ],
                        "第7章 三角函数": [
                            "7.1 角与弧度",
                            "7.2 三角函数概念",
                            "7.3 三角函数的图象和性质",
                            "7.4 三角函数应用"
                        ],
                        "第8章 函数应用": [
                            "8.1 二分法与求方程近似解",
                            "8.2 函数与数学模型"
                        ]
                    },
                    "必修二": {
                        "第9章 平面向量": [
                            "9.1 向量概念",
                            "9.2 向量运算",
                            "9.3 向量基本定理及坐标表示",
                            "9.4 向量应用"
                        ],
                        "第10章 三角恒等变换": [
                            "10.1 两角和与差的三角函数",
                            "10.2 二倍角的三角函数",
                            "10.3 几个三角恒等式"
                        ],
                        "第11章 解三角形": [
                            "11.1 余弦定理",
                            "11.2 正弦定理",
                            "11.3 余弦定理、正弦定理的应用"
                        ],
                        "第12章 复数": [
                            "12.1 复数的概念",
                            "12.2 复数的运算",
                            "12.3 复数的几何意义",
                            "12.4 复数的三角形式"
                        ],
                        "第13章 立体几何初步": [
                            "13.1 基本立体图形",
                            "13.2 基本图形位置关系",
                            "13.3 空间图形的表面积和体积"
                        ],
                        "第14章 统计": [
                            "14.1 获取数据的基本途径及相关概念",
                            "14.2 抽样",
                            "14.3 统计图表",
                            "14.4 用样本估计总体"
                        ],
                        "第15章 概率": [
                            "15.1 样本空间和随机事件",
                            "15.2 随机事件的概率",
                            "15.3 互斥事件和独立事件"
                        ]
                    },
                    "选修一": {
                        "第1章 直线与方程": [
                            "1.1 直线的斜率与倾斜角",
                            "1.2 直线的方程",
                            "1.3 两条直线的平行与垂直",
                            "1.4 两条直线的交点",
                            "1.5 平面上的距离"
                        ],
                        "第2章 圆与方程": [
                            "2.1 圆的方程",
                            "2.2 直线与圆的位置关系",
                            "2.3 圆与圆的位置关系"
                        ],
                        "第3章 圆锥曲线与方程": [
                            "3.1 椭圆",
                            "3.2 双曲线",
                            "3.3 抛物线"
                        ],
                        "第4章 数列": [
                            "4.1 数列",
                            "4.2 等差数列",
                            "4.3 等比数列",
                            "4.4 数学归纳法"
                        ],
                        "第5章 导数及其应用": [
                            "5.1 导数的概念",
                            "5.2 导数的运算",
                            "5.3 导数在研究函数中的应用"
                        ]
                    },
                    "选修二": {
                        "第6章 空间向量与立体几何": [
                            "6.1 空间向量及其运算",
                            "6.2 空间向量的坐标表示",
                            "6.3 空间向量的应用"
                        ],
                        "第7章 计数原理": [
                            "7.1 两个基本计数原理",
                            "7.2 排列",
                            "7.3 组合",
                            "7.4 二项式定理"
                        ],
                        "第8章 概率": [
                            "8.1 条件概率",
                            "8.2 离散型随机变量及其分布列",
                            "8.3 正态分布"
                        ],
                        "第9章 统计": [
                            "9.1 线性回归分析",
                            "9.2 独立性检验"
                        ]
                    }
                };

                const selectedCurriculum = version === 'B' ? curriculumB : (version === 'S' ? curriculumS : curriculumA);
                
                const defaultMetadata = {
                    "question_types": [
                        {"value": "single_choice", "label": "单选题"},
                        {"value": "multi_choice", "label": "多选题"},
                        {"value": "fill_in_blank", "label": "填空题"},
                        {"value": "detailed_answer", "label": "解答题"}
                    ],
                    "difficulties": [
                        {"value": "easy_error", "label": "易错题", "color": "text-green-600 bg-green-50 border-green-200"},
                        {"value": "normal", "label": "常规题", "color": "text-blue-600 bg-blue-50 border-blue-200"},
                        {"value": "challenge", "label": "挑战题", "color": "text-red-600 bg-red-50 border-red-200"},
                        {"value": "qiangji", "label": "强基题", "color": "text-purple-600 bg-purple-50 border-purple-200"}
                    ],
                    "curriculum": selectedCurriculum
                };
                
                document.getElementById('settingsMetadataJson').value = JSON.stringify(defaultMetadata, null, 2);
                showToast(`已加载默认【${versionName}】配置模板，请点击最下方的 [保存配置] 按钮进行保存并应用。`);
            }
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
        function getDifficultyBadge(diff) {
            if (!systemMetadata || !systemMetadata.difficulties || systemMetadata.difficulties.length === 0) {
                if (diff === 'easy_error') return '<span class="text-[9px] font-bold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">易错</span>';
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
            const themes = ['theme-violet', 'theme-emerald', 'theme-ocean', 'theme-amber', 'theme-crimson'];
            
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
                    'violet': '#8b5cf6',
                    'ocean': '#0ea5e9',
                    'emerald': '#10b981',
                    'amber': '#f59e0b',
                    'crimson': '#f43f5e'
                };
                dot.style.backgroundColor = colorMap[themeName] || '#8b5cf6';
                nameSpan.textContent = getThemeChineseName(themeName);
            }
            
            // Update check marks
            const themesOnly = ['violet', 'ocean', 'emerald', 'amber', 'crimson'];
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

        // Initialize theme on DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {
            initTheme();
        });

