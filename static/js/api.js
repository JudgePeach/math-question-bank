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
        let uploadedImages = [];
        let uploadedAnswerImages = [];
        let originalQuestionState = null;
        let contentOcrAbortController = null;
        let answerOcrAbortController = null;

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
            simpletex: [
                "simpletex"
            ],
            zhongzhan_gpt: [],
            zhongzhan_claude: []
        };

        // 从 localStorage 恢复自定义模型，或者初始化
        function getModelListForProvider(provider) {
            const presets = MODEL_PRESETS[provider] || [];
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            const customs = customStr ? JSON.parse(customStr) : [];
            return Array.from(new Set([...presets, ...customs]));
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

        // 动态装载模型选择/手写输入区域
        function renderModelSelector(typeKey, provider, selectedValue = "") {
            const container = document.getElementById(`${typeKey}ModelValueContainer`);
            if (!container) return;
            
            const isZhongzhan = provider === 'zhongzhan' || provider === 'zhongzhan_gpt' || provider === 'zhongzhan_claude';
            
            if (isZhongzhan) {
                // 中转站模式：采用 input + datalist 实现“可写、可点选历史记录”的高效设计
                const datalistId = `datalist_${typeKey}_${provider}`;
                const models = getModelListForProvider(provider);
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
            } else if (provider === 'simpletex') {
                // SimpleTex 专属 (只读)
                container.innerHTML = `
                    <input type="text" readonly value="SimpleTex 官方接口"
                           class="glass-input flex-1 px-2.5 py-1.5 rounded-lg text-xs bg-slate-100 text-slate-500 cursor-not-allowed">
                `;
            } else {
                // 下拉菜单列表模式
                const models = getModelListForProvider(provider);
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
                } else if (provider === 'simpletex') {
                    return 'simpletex';
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
            
            fetch('/api/settings/save', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast(data.message);
                    closeSettingsModal();
                    fetchConfigStatus();
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                showToast('保存接口设置出错: ' + err, 'error');
            });
        }

        // Load Categories from Database to Autocomplete Selects
        function loadCategories(retryCount = 0) {
            fetch('/api/categories')
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
                })
                .catch(err => {
                    console.error('加载分类目录树发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载分类数据...`);
                        setTimeout(() => loadCategories(retryCount + 1), 1500);
                    } else {
                        showToast('系统正在连接或初始化后台，加载分类失败，请刷新重试', 'error');
                    }
                });
        }

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
            if (diff === 'easy_error') return '<span class="text-[9px] font-bold text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">易错</span>';
            if (diff === 'challenge') return '<span class="text-[9px] font-bold text-red-600 bg-red-50 px-1.5 py-0.5 rounded">挑战</span>';
            if (diff === 'qiangji') return '<span class="text-[9px] font-bold text-purple-600 bg-purple-50 px-1.5 py-0.5 rounded">强基</span>';
            return '<span class="text-[9px] font-bold text-slate-550 bg-slate-100 px-1.5 py-0.5 rounded">未定</span>';
        }

        function getTypeText(type) {
            if (type === 'single_choice') return '单选题';
            if (type === 'multi_choice') return '多选题';
            if (type === 'fill_in_blank') return '填空题';
            if (type === 'detailed_answer') return '解答题';
            return '数学题';
        }

        // Debounced Live Realtime Preview Setup
        function getDifficultyText(val) {
            if (val === 'easy_error') return '易错题';
            if (val === 'challenge') return '挑战题';
            if (val === 'qiangji') return '强基题';
            return '未定';
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

