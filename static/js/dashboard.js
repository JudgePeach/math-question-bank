/**
 * dashboard.js - 教学准备工作台
 * 只读取现有题库统计与试卷记录接口，不改变原有业务数据或工作流。
 */
(function () {
    'use strict';

    const numberFormatter = new Intl.NumberFormat('zh-CN');

    function escapeText(value) {
        if (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function') {
            return window.MathBankSafe.escapeText(value == null ? '' : String(value));
        }
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatCount(value) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numberFormatter.format(numeric) : '--';
    }

    function formatDate(value) {
        if (!value) return '时间未知';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '时间未知';
        return date.toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function monthAdditionCount(dailyAdds) {
        if (!dailyAdds || typeof dailyAdds !== 'object') return 0;
        const monthKey = new Date().toISOString().slice(0, 7);
        return Object.entries(dailyAdds).reduce((total, [dateKey, count]) => {
            return dateKey.startsWith(monthKey) ? total + (Number(count) || 0) : total;
        }, 0);
    }

    function renderTaskList(papers) {
        const container = document.getElementById('dashboardTaskList');
        if (!container) return;

        const recentPapers = Array.isArray(papers) ? papers.slice(0, 3) : [];
        if (recentPapers.length === 0) {
            container.innerHTML = `
                <div class="ui-state ui-state-empty">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-clipboard-list"></i></span>
                    <strong class="ui-state-title">还没有进行中的任务</strong>
                    <span class="ui-state-description">可以从右侧快速开始导入试卷、录入题目或创建试卷草稿。</span>
                    <button type="button" class="ui-state-action dashboard-state-action" onclick="selectWorkspace('import', '导入中心')">
                        <i class="fa-solid fa-plus" aria-hidden="true"></i><span>新建任务</span>
                    </button>
                </div>`;
            return;
        }

        container.innerHTML = recentPapers.map((paper) => {
            const paperId = Number.parseInt(paper && paper.id, 10);
            const title = escapeText(paper && paper.title ? paper.title : '未命名试卷');
            const count = formatCount(paper && paper.question_count);
            const date = escapeText(formatDate(paper && paper.created_at));
            const canResume = Number.isFinite(paperId) && paperId > 0;
            return `
                <article class="dashboard-task-card">
                    <div class="dashboard-task-card-heading">
                        <div>
                            <strong>${title}</strong>
                            <span>已保存 ${count} 题 · ${date}</span>
                        </div>
                        ${canResume ? `<button type="button" class="dashboard-task-resume" onclick="resumeSavedPaper(${paperId})">继续编排</button>` : ''}
                    </div>
                    <div class="dashboard-task-progress-row">
                        <span>试卷草稿已保存</span><strong>100%</strong>
                    </div>
                    <div class="dashboard-progress-track" aria-hidden="true"><span style="width:100%"></span></div>
                </article>`;
        }).join('');
    }

    function renderActivity(papers, stats) {
        const container = document.getElementById('dashboardActivityList');
        if (!container) return;

        const activities = [];
        const monthlyAdds = monthAdditionCount(stats && stats.daily_adds);
        if (monthlyAdds > 0) {
            activities.push({
                icon: 'fa-database',
                title: `本月新增题目 ${formatCount(monthlyAdds)} 道`,
                meta: '题库统计'
            });
        }
        (Array.isArray(papers) ? papers : []).slice(0, 3).forEach((paper) => {
            activities.push({
                icon: 'fa-file-lines',
                title: `保存试卷 · ${paper && paper.title ? paper.title : '未命名试卷'}`,
                meta: formatDate(paper && paper.created_at)
            });
        });

        if (activities.length === 0) {
            container.innerHTML = `
                <div class="ui-state ui-state-empty">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-regular fa-clock"></i></span>
                    <span class="ui-state-description">暂无最近动态，完成一次导入或保存试卷后会显示在这里。</span>
                </div>`;
            return;
        }

        container.innerHTML = activities.slice(0, 4).map((activity) => `
            <div class="dashboard-activity-item">
                <span class="dashboard-activity-icon" aria-hidden="true"><i class="fa-solid ${escapeText(activity.icon)}"></i></span>
                <div><strong>${escapeText(activity.title)}</strong><small>${escapeText(activity.meta)}</small></div>
            </div>`).join('');
    }

    function renderDashboardError(message) {
        const safeMessage = escapeText(message || '后台数据暂时不可用');
        ['dashboardTaskList', 'dashboardActivityList'].forEach((id) => {
            const container = document.getElementById(id);
            if (!container) return;
            container.innerHTML = `
                <div class="ui-state ui-state-error">
                    <span class="ui-state-icon" aria-hidden="true"><i class="fa-solid fa-triangle-exclamation"></i></span>
                    <strong class="ui-state-title">工作台数据加载失败</strong>
                    <span class="ui-state-description">${safeMessage}</span>
                    <button type="button" class="ui-state-action dashboard-state-action" onclick="loadDashboardData()">
                        <i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i><span>重新加载</span>
                    </button>
                </div>`;
        });
    }

    async function loadDashboardData() {
        const taskContainer = document.getElementById('dashboardTaskList');
        const activityContainer = document.getElementById('dashboardActivityList');
        if (!taskContainer || !activityContainer) return;

        taskContainer.setAttribute('aria-busy', 'true');
        activityContainer.setAttribute('aria-busy', 'true');
        try {
            const [statsResponse, papersResponse] = await Promise.all([
                fetch('/api/stats'),
                fetch('/api/papers')
            ]);
            if (!statsResponse.ok || !papersResponse.ok) {
                throw new Error('后台统计接口暂时不可用');
            }
            const stats = await statsResponse.json();
            const paperPayload = await papersResponse.json();
            if (stats.status && stats.status !== 'success') {
                throw new Error(stats.message || '题库统计读取失败');
            }
            if (paperPayload.status && paperPayload.status !== 'success') {
                throw new Error(paperPayload.message || '试卷记录读取失败');
            }

            const papers = Array.isArray(paperPayload.data) ? paperPayload.data : [];
            const monthlyAdds = monthAdditionCount(stats.daily_adds);
            setText('dashboardQuestionTotal', formatCount(stats.total_count));
            setText('dashboardQuestionTotalMeta', `当前本地题库 · ${formatCount(stats.normal_count)} 道常规题`);
            setText('dashboardReviewCount', formatCount(stats.easy_error_count));
            setText('dashboardReviewMeta', '易错题可返回题库复核');
            setText('dashboardPaperCount', formatCount(papers.length));
            setText('dashboardPaperMeta', papers.length ? '可从试卷记录继续编排' : '还没有保存的试卷');
            setText('dashboardMonthAdditions', formatCount(monthlyAdds));
            setText('dashboardMonthMeta', monthlyAdds ? '本月新增题目' : '本月暂无新增题目');
            renderTaskList(papers);
            renderActivity(papers, stats);
        } catch (error) {
            console.error('加载工作台数据失败:', error);
            renderDashboardError(error && error.message);
        } finally {
            taskContainer.removeAttribute('aria-busy');
            activityContainer.removeAttribute('aria-busy');
        }
    }

    // Dashboard quick actions must switch to the target workspace before opening
    // a modal or loading a saved paper.  Calling the underlying actions directly
    // while the dashboard is active leaves the target workspace hidden, which can
    // make the app appear frozen behind the toast notification.
    function startManualQuestion() {
        if (typeof window.selectWorkspace === 'function') {
            window.selectWorkspace('bank', '题库管理');
        }
        window.requestAnimationFrame(() => {
            if (typeof window.openNewQuestionEditor === 'function') {
                window.openNewQuestionEditor();
            }
        });
    }

    async function resumeSavedPaper(paperId) {
        if (typeof window.selectWorkspace === 'function') {
            window.selectWorkspace('paper', '智能组卷');
        }
        if (typeof window.loadSavedPaper === 'function') {
            await window.loadSavedPaper(paperId);
        }
    }

    window.loadDashboardData = loadDashboardData;
    window.startManualQuestion = startManualQuestion;
    window.resumeSavedPaper = resumeSavedPaper;

    document.addEventListener('DOMContentLoaded', () => {
        loadDashboardData();
    });
})();
