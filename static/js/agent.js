/* Conversation and explicit, server-bound change review. No chat HTML is trusted. */
(function () {
    'use strict';
    let sessionId = null;
    let busy = false;
    let activeProposal = null;
    let configGeneration = 0;
    let followOutput = true;
    let scrollFrame = null;
    const labels = {tags: '标签', difficulty: '难度', answer_markdown: '解析', review: '评析'};
    const difficulties = {normal: '常规题', easy_error: '易错题', challenge: '挑战题', qiangji: '强基题'};
    const paperTypes = {exam: '常规试卷', quiz: '日常小练', exam_19: '19题高考卷'};
    const byId = id => document.getElementById(id);

    function scheduleOutputScroll() {
        if (!followOutput || scrollFrame !== null) return;
        scrollFrame = requestAnimationFrame(() => {
            scrollFrame = null;
            const messages = byId('agentMessages');
            if (followOutput && messages?.clientHeight) messages.scrollTop = messages.scrollHeight;
        });
    }

    function setFollowOutput(enabled) {
        followOutput = enabled;
        byId('agentFollowOutput')?.setAttribute('aria-pressed', String(enabled));
        if (byId('agentFollowLabel')) byId('agentFollowLabel').textContent = enabled ? '跟随输出：开启' : '跟随输出：暂停';
        if (enabled) scheduleOutputScroll();
    }

    function initOutputScroll() {
        const messages = byId('agentMessages');
        let lastTop = messages.scrollTop;
        const atBottom = () => messages.scrollHeight - messages.clientHeight - messages.scrollTop <= 8;
        const pause = () => { if (followOutput) setFollowOutput(false); };
        messages.addEventListener('scroll', () => {
            const top = messages.scrollTop;
            if (top < lastTop - 1 && !atBottom()) pause();
            else if (top > lastTop + 1 && atBottom() && !followOutput) setFollowOutput(true);
            lastTop = top;
        }, {passive: true});
        // Pause before a queued follow frame can undo a user's upward gesture.
        // This also lets users inspect the nested raw-output pane uninterrupted.
        messages.addEventListener('wheel', event => { if (event.deltaY < 0) pause(); }, {passive: true});
        let touchY = null;
        messages.addEventListener('touchstart', event => { touchY = event.touches[0]?.clientY ?? null; }, {passive: true});
        messages.addEventListener('touchmove', event => {
            const nextY = event.touches[0]?.clientY;
            if (touchY !== null && nextY > touchY + 2) pause();
            touchY = nextY ?? null;
        }, {passive: true});
        messages.addEventListener('keydown', event => {
            if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) pause();
        });
        byId('agentFollowOutput').addEventListener('click', () => setFollowOutput(!followOutput));
        byId('agentScrollBottom').addEventListener('click', () => setFollowOutput(true));
        new MutationObserver(scheduleOutputScroll).observe(messages, {childList: true, subtree: true, characterData: true});
        messages.addEventListener('load', scheduleOutputScroll, true);
        messages.addEventListener('toggle', scheduleOutputScroll, true);
        if (typeof ResizeObserver === 'function') new ResizeObserver(scheduleOutputScroll).observe(messages);
        window.addEventListener('resize', scheduleOutputScroll, {passive: true});
        scheduleOutputScroll();
    }

    async function request(path, body) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), body ? 240000 : 15000);
        try {
            const response = await fetch('/api/agent/' + path, {
                method: body ? 'POST' : 'GET', signal: controller.signal,
                headers: body ? {'Content-Type': 'application/json', 'X-Local-Token': window.__localToken} : {},
                ...(body ? {body: JSON.stringify(body)} : {})
            });
            const data = await response.json();
            if (!response.ok) {
                const error = new Error(typeof data.detail === 'string' ? data.detail : (data.message || '请求失败，请稍后重试。'));
                error.status = response.status;
                throw error;
            }
            return data;
        } catch (error) {
            if (error.name === 'AbortError' || error instanceof TypeError) {
                throw new Error('连接中断或等待超时。服务端可能仍在处理；若已确认写入，请先检查题库再操作。');
            }
            throw error;
        } finally { clearTimeout(timer); }
    }

    async function streamChat(body, handlers) {
        const controller = new AbortController();
        let timer;
        const resetIdleTimeout = () => {
            clearTimeout(timer);
            timer = setTimeout(() => controller.abort(), 60000);
        };
        resetIdleTimeout();
        try {
            const response = await fetch('/api/agent/chat/stream', {
                method: 'POST', signal: controller.signal,
                headers: {'Content-Type': 'application/json', 'X-Local-Token': window.__localToken},
                body: JSON.stringify(body)
            });
            if (!response.ok) {
                let detail = '流式请求未建立。';
                try {
                    const payload = await response.json();
                    detail = typeof payload.detail === 'string' ? payload.detail : detail;
                } catch (_) { }
                throw new Error(detail);
            }
            if (!response.body) throw new Error('当前浏览器不支持流式响应，请稍后重试。');
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let finalResult = null;
            let ended = false;
            const dispatch = block => {
                const lines = block.split(/\r?\n/);
                let eventName = 'message';
                const data = [];
                lines.forEach(line => {
                    if (line.startsWith('event:')) eventName = line.slice(6).trim();
                    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
                });
                if (!data.length) return;
                let payload;
                try { payload = JSON.parse(data.join('\n')); }
                catch (_) { throw new Error('助手流式事件格式无效。'); }
                if (eventName === 'result') finalResult = payload;
                if (eventName === 'error') {
                    ended = true;
                    throw new Error(payload.message || '助手请求未完成。');
                }
                if (handlers[eventName]) handlers[eventName](payload);
            };
            while (true) {
                const chunk = await reader.read();
                if (!chunk.done && chunk.value?.length) resetIdleTimeout();
                buffer += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
                const blocks = buffer.split(/\r?\n\r?\n/);
                buffer = blocks.pop() || '';
                blocks.forEach(dispatch);
                if (chunk.done) break;
            }
            if (buffer.trim()) dispatch(buffer);
            if (!finalResult && !ended) throw new Error('助手流式响应提前结束。');
            return finalResult;
        } catch (error) {
            if (error.name === 'AbortError' || error instanceof TypeError) {
                throw new Error('连接中断或等待超时。服务端可能仍在处理；若已确认写入，请先检查题库再操作。');
            }
            throw error;
        } finally { clearTimeout(timer); controller.abort(); }
    }

    function setBusy(value, message) {
        busy = value;
        ['agentSend', 'agentNewChat'].forEach(id => { byId(id).disabled = value; });
        byId('agentForm').setAttribute('aria-busy', String(value));
        byId('agentStatus').textContent = message || '';
        document.querySelectorAll('[data-agent-apply]').forEach(button => { button.disabled = value || button.dataset.finished === 'true'; });
    }

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function renderMarkdown(container, text) {
        container.classList.add('agent-markdown');
        try {
            if (typeof window.renderMarkdownPreviewContent === 'function') {
                window.renderMarkdownPreviewContent(container, text);
                return;
            }
        } catch (_) { /* Keep the source readable if a preview cannot render. */ }
        container.textContent = text;
    }

    // Decode only a root reply string, never a tool argument or nested value.
    // Incomplete JSON escapes wait for the next chunk; nothing here authorizes
    // an action. The final server result remains authoritative.
    function partialReply(source) {
        const header = /^\s*(?:```(?:json)?\s*)?\{\s*"reply"\s*:\s*"/.exec(source);
        if (!header) return null;
        const escapes = {'"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t'};
        let text = '';
        for (let index = header[0].length; index < source.length; index++) {
            const char = source[index];
            if (char === '"') break;
            if (char.charCodeAt(0) < 32) return null;
            if (char !== '\\') { text += char; continue; }
            if (++index >= source.length) break;
            const escaped = source[index];
            if (escaped === 'u') {
                const code = source.slice(index + 1, index + 5);
                if (code.length < 4) break;
                if (!/^[0-9a-f]{4}$/i.test(code)) return null;
                text += String.fromCharCode(parseInt(code, 16));
                index += 4;
            } else if (Object.prototype.hasOwnProperty.call(escapes, escaped)) text += escapes[escaped];
            else return null;
        }
        if (/[\uD800-\uDBFF]$/.test(text)) text = text.slice(0, -1);
        return text.slice(0, 12000);
    }

    function appendMessage(role, text) {
        byId('agentWelcome')?.remove();
        const article = node('article', 'agent-message agent-message-' + role);
        article.append(node('strong', 'agent-speaker', role === 'user' ? '你' : '题库助手'));
        const content = node('div', 'agent-message-text');
        renderMarkdown(content, text);
        article.append(content);
        byId('agentMessages').append(article);
        scheduleOutputScroll();
        return article;
    }

    function appendLiveProcess(parent) {
        const details = node('details', 'agent-live');
        details.open = true;
        details.append(node('summary', '', '实时处理过程'));
        const log = node('pre', 'agent-live-log');
        const model = node('pre', 'agent-live-model');
        model.hidden = true;
        details.append(log, model);
        parent.append(details);
        return {
            finish: () => { details.open = false; },
            status: message => { if (message) { log.textContent += (log.textContent ? '\n' : '') + message; } },
            model: text => { model.hidden = false; model.textContent += text; if (followOutput) model.scrollTop = model.scrollHeight; },
            complete: chars => {
                log.textContent += (log.textContent ? '\n' : '') + `模型输出已接收（${chars} 字符）`;
                model.textContent += '\n\n';
            },
            tool: payload => {
                const labels = {search_questions: '检索题目', get_question: '读取题目详情', get_questions: '批量读取题目详情', statistics: '统计题库', propose_changes: '校验修改方案', propose_answer: '校验解析修改方案', propose_delete: '校验删除方案', propose_paper: '校验组卷方案'};
                const label = labels[payload.name] || '校验助手指令';
                let line = `${label}${payload.status === 'rejected' ? '未通过' : '完成'}`;
                if (payload.count != null) line += ` · ${payload.count} 题`;
                if (payload.id != null) line += ` · 稳定 ID ${payload.id}`;
                if (payload.ids) line += ` · 稳定 ID ${payload.ids.join('、')}`;
                if (payload.by_difficulty) line += ' · ' + Object.entries(payload.by_difficulty).map(([key, value]) => `${difficulties[key] || key} ${value} 题`).join('，');
                if (payload.error) line += ` · ${payload.error}`;
                if (payload.message) line += ` · ${payload.message}`;
                log.textContent += (log.textContent ? '\n' : '') + line;
            }
        };
    }

    function expireProposal() {
        if (!activeProposal) return;
        activeProposal.button.disabled = true;
        activeProposal.button.dataset.finished = 'true';
        activeProposal.note.textContent = '已被后续对话替代，请使用新的方案。';
        activeProposal = null;
    }

    function renderProposal(parent, proposal) {
        const kind = proposal.kind || 'update';
        const deleting = kind === 'delete', paper = kind === 'paper';
        const panel = node('section', 'agent-proposal');
        panel.dataset.agentKind = kind;
        panel.append(node('h3', '', `待确认：${paper ? '组卷' : deleting ? '删除' : '修改'} ${proposal.changes.length} 题`));
        if (paper) {
            panel.append(node('p', 'agent-paper-title', proposal.paper.title));
            if (proposal.paper.subtitle) panel.append(node('p', 'agent-status', proposal.paper.subtitle));
            panel.append(node('p', 'agent-status', `${paperTypes[proposal.paper.paper_type]} · 总分 ${proposal.paper.total_score} 分 · 确认后保存到试卷记录，可再载入工作台排版。`));
        }
        if (deleting) panel.append(node('p', 'agent-delete-impact', '确认后将从题库及已保存试卷中删除这些题目，并重新计算相关试卷总分；已有题文引用将显示目标已删除。可通过完整备份恢复。'));
        proposal.changes.forEach((change, index) => {
            const details = node('details', 'agent-change');
            details.open = true;
            details.append(node('summary', '', `${paper ? `第 ${index + 1} 题 · ` : ''}稳定 ID ${change.id} · ${paper ? `${change.score} 分` : deleting ? '删除题目' : Object.keys(change.patch).map(key => labels[key]).join('、')}`));
            const reason = node('div', 'agent-reason');
            renderMarkdown(reason, change.reason);
            details.append(reason);
            if (paper || deleting) {
                const preview = node('div', 'agent-field-preview');
                if (typeof window.renderQuestionPreviewContent === 'function') {
                    window.renderQuestionPreviewContent(preview, change.content);
                } else renderMarkdown(preview, change.content);
                details.append(node('p', 'agent-status', `来源：${change.source || '未填写'} · ${difficulties[change.difficulty] || change.difficulty}`), preview);
            }
            if (deleting) {
                details.append(node('p', 'agent-delete-impact', `影响 ${change.impact.paper_count} 份已保存试卷、${change.impact.reference_count} 处题文引用（${change.impact.incoming_reference_count || 0} 处指向此题，${change.impact.outgoing_reference_count || 0} 处来自此题）。`));
                change.impact.papers.forEach(item => details.append(node('p', 'agent-status', `试卷 ID ${item.paper_id}《${item.title}》· 移除第 ${item.order_index} 题（${item.score} 分）`)));
                if (change.impact.paper_count > 20) details.append(node('p', 'agent-status', '以上仅展示前 20 条试卷引用，确认会作用于全部受影响试卷。'));
            }
            Object.entries(change.patch || {}).forEach(([key, value]) => {
                const field = node('div', 'agent-field');
                field.append(node('h4', '', labels[key]));
                [['原值', change.before[key]], ['新值', value]].forEach(([title, text]) => {
                    field.append(node('small', '', title));
                    if (key === 'answer_markdown' || key === 'review') {
                        const preview = node('div', 'agent-field-preview');
                        renderMarkdown(preview, text || '（空）');
                        const source = node('details', 'agent-source');
                        source.append(node('summary', '', '查看原始文本'), node('pre', '', text || '（空）'));
                        field.append(preview, source);
                    } else {
                        field.append(node('pre', '', key === 'difficulty' ? (difficulties[text] || text) : (text || '（空）')));
                    }
                });
                details.append(field);
            });
            panel.append(details);
        });
        const actions = node('div', 'agent-proposal-actions');
        const button = node('button', deleting ? 'agent-delete-button' : 'glass-btn-primary', paper ? '确认组卷并保存试卷' : deleting ? `确认删除这 ${proposal.changes.length} 题` : '确认写入这份方案');
        button.type = 'button';
        button.dataset.agentApply = proposal.id;
        const note = node('p', 'agent-status', '写入前自动创建完整备份；题目已变化时整份方案会停止。');
        const context = {button, note};
        activeProposal = context;
        button.addEventListener('click', async () => {
            if (busy || button.dataset.finished === 'true') return;
            const editsCurrent = !paper && proposal.changes.some(change => change.id === window.EditorState?.questionId);
            if (editsCurrent && (window.isEditorModified?.() || window.isQuestionSaveInFlight?.())) {
                note.textContent = '这道题在编辑器中有未保存内容或正在保存。请先处理编辑，再确认方案。';
                return;
            }
            setBusy(true, '正在检查题目、创建备份并写入…');
            try {
                const result = await request('apply', {session_id: sessionId, proposal_id: proposal.id});
                button.dataset.finished = 'true';
                button.textContent = paper ? '已保存试卷' : deleting ? '已删除' : '已写入';
                note.textContent = (paper ? `试卷已保存（ID ${result.paper_id}），共 ${result.question_count} 题、${result.total_score} 分。` : deleting ? `已删除 ${result.deleted} 题。` : `已更新 ${result.updated} 题。`) + (result.backup ? `备份：${result.backup}` : '');
                activeProposal = null;
                try { refreshQuestionViews(proposal.changes, kind); }
                catch (_) { note.textContent += ' 页面刷新未完成，请重新打开题目查看。'; }
                if (paper) {
                    const open = node('button', 'glass-btn', '查看试卷记录');
                    open.type = 'button';
                    open.dataset.agentPaper = String(result.paper_id);
                    open.addEventListener('click', () => window.openSavedPapersModal?.());
                    actions.append(open);
                }
                setBusy(false, '方案已写入题库。');
            } catch (error) {
                // Keep the exact capability for an idempotent replay; never generate a replacement automatically.
                note.textContent = error.message;
                setBusy(false, '写入未得到成功确认，请核对提示。');
            }
        });
        actions.append(button);
        panel.append(actions, note);
        parent.append(panel);
        // Previews were built detached. Measure only after their actual
        // container width is available and register responsive observations.
        window.adaptChoicesGridLayout?.(panel);
    }

    function refreshQuestionViews(changes, kind = 'update') {
        const store = window.PaperStore;
        changes.forEach(change => {
            if (kind === 'delete') {
                if (window.isInCart?.(change.id)) window.removeFromCart?.(change.id);
                if (store) {
                    delete store.questionsMap[change.id];
                    delete store.answerCache[change.id];
                    store.expandedAnswerIds.delete(change.id);
                    store.bankQuestions = store.bankQuestions.filter(item => item.id !== change.id);
                }
                return;
            }
            const question = store?.questionsMap?.[change.id];
            if (kind === 'paper') {
                if (question) question.usage_count = (question.usage_count || 0) + 1;
                return;
            }
            if (question) Object.assign(question, change.patch);
            (store?.bankQuestions || []).filter(item => item.id === change.id).forEach(item => Object.assign(item, change.patch));
            if (store && Object.prototype.hasOwnProperty.call(change.patch, 'answer_markdown')) {
                store.answerCache[change.id] = change.patch.answer_markdown;
                if (question) question.has_answer = Boolean(change.patch.answer_markdown.trim());
            }
        });
        if (typeof window.loadQuestions === 'function' && (typeof activeSidebarTab === 'undefined' || activeSidebarTab === 'bank')) window.loadQuestions();
        const currentId = window.EditorState?.questionId;
        if (kind !== 'paper' && changes.some(change => change.id === currentId) && !window.EditorState?.draftId
                && !window.isEditorModified?.() && !window.isQuestionSaveInFlight?.()
                && typeof window.selectQuestion === 'function') {
            if (kind === 'delete') {
                window.startNewQuestionAfterAgentDelete?.();
                window.closeBankDetail?.();
            }
            else window.selectQuestion({id: currentId}, {silent: true});
        }
        window.dispatchEvent(new CustomEvent('mathbank-question-mutated'));
    }

    window.loadAgentConfig = async function () {
        const generation = ++configGeneration;
        try {
            const config = await request('config');
            if (generation !== configGeneration) return;
            byId('agentModel').textContent = `${config.model} · 工具调用不限次数`;
            if (!config.configured && !busy) byId('agentStatus').textContent = '请先在系统设置中配置解题模型和对应 API 密钥。';
        } catch (error) {
            if (generation === configGeneration) byId('agentModel').textContent = '配置读取失败，可重新进入助手重试。';
        }
    };

    function init() {
        if (!byId('agentForm')) return;
        initOutputScroll();
        document.querySelectorAll('[data-agent-example]').forEach(button => button.addEventListener('click', () => {
            byId('agentInput').value = button.dataset.agentExample;
            byId('agentInput').focus();
        }));
        byId('agentNewChat').addEventListener('click', async () => {
            if (busy) return;
            if (sessionId) {
                setBusy(true, '正在结束当前对话…');
                try { await request('session/close', {session_id: sessionId}); }
                catch (error) { setBusy(false, error.message); return; }
                setBusy(false);
            }
            expireProposal();
            sessionId = null;
            setFollowOutput(true);
            byId('agentMessages').replaceChildren();
            appendMessage('assistant', '已开始新对话。请输入需要查找或整理的题库任务。');
            byId('agentStatus').textContent = '';
            byId('agentInput').focus();
        });
        byId('agentInput').addEventListener('keydown', event => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
                event.preventDefault();
                byId('agentForm').requestSubmit();
            }
        });
        byId('agentForm').addEventListener('submit', async event => {
            event.preventDefault();
            const message = byId('agentInput').value.trim();
            if (busy || !message) return;
            setFollowOutput(true);
            appendMessage('user', message);
            const submitted = byId('agentInput').value;
            setBusy(true, '正在查阅题库并处理请求，修改会先生成方案…');
            const processArticle = appendMessage('assistant', '正在连接模型…');
            const content = processArticle.querySelector('.agent-message-text');
            let modelText = '', replyText = null, renderTimer = null;
            const cancelRender = () => { clearTimeout(renderTimer); renderTimer = null; };
            const renderReply = () => {
                cancelRender();
                const reply = partialReply(modelText);
                if (reply !== null) { replyText = reply; renderMarkdown(content, reply); }
            };
            try {
                processArticle.setAttribute('aria-busy', 'true');
                const live = appendLiveProcess(processArticle);
                const result = await streamChat({message, session_id: sessionId}, {
                    session: payload => { sessionId = payload.session_id; },
                    status: payload => {
                        live.status(payload.message);
                        byId('agentStatus').textContent = payload.message || '';
                        if (replyText === null) content.textContent = payload.message || '正在处理…';
                    },
                    model_delta: payload => {
                        const delta = payload.text || '';
                        live.model(delta);
                        modelText += delta;
                        if (renderTimer === null) renderTimer = setTimeout(renderReply, 80);
                    },
                    model_complete: payload => {
                        renderReply();
                        modelText = '';
                        live.complete(payload.characters || 0);
                    },
                    tool: payload => live.tool(payload),
                });
                sessionId = result.session_id;
                live.finish();
                expireProposal();
                if (byId('agentInput').value === submitted) byId('agentInput').value = '';
                cancelRender();
                renderMarkdown(content, result.reply);
                processArticle.removeAttribute('aria-busy');
                const article = processArticle;
                if (result.trace.length) {
                    const details = node('details', 'agent-trace');
                    details.append(node('summary', '', `已执行 ${result.trace.length} 次题库查询 / 校验`));
                    const names = {search_questions: '检索题目', get_question: '读取题目', get_questions: '批量读取题目', statistics: '统计', rejected: '指令校验未通过'};
                    result.trace.forEach(item => {
                        details.append(node('p', '', (names[item.tool] || '校验') + (item.count != null ? ` · ${item.count} 题` : '') + (item.id ? ` · 稳定 ID ${item.id}` : '')));
                        (item.items || []).forEach(question => {
                            const row = node('div', 'agent-search-result');
                            row.append(node('strong', '', `稳定 ID ${question.id}`), node('p', '', question.content), node('small', '', question.source || '未填写来源'));
                            const open = node('button', 'agent-open-question', '在题库中打开');
                            open.type = 'button';
                            open.addEventListener('click', () => {
                                window.selectWorkspace('bank', '题库管理');
                                if (typeof window.selectQuestionById === 'function') window.selectQuestionById(question.id);
                            });
                            row.append(open);
                            details.append(row);
                        });
                    });
                    article.append(details);
                }
                if (result.proposal) renderProposal(article, result.proposal);
                setBusy(false, result.proposal ? '方案尚未写入，请逐题核对。' : '本轮完成。');
            } catch (error) {
                cancelRender();
                processArticle.removeAttribute('aria-busy');
                content.textContent = error.message;
                setBusy(false, '请求未完成。你输入的文字已保留。');
            }
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
