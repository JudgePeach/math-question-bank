/* Offline FAQ workspace. Render curated text with DOM APIs, never chat HTML. */
(function () {
    'use strict';

    function initQaWorkspace() {
        const section = document.getElementById('qaWorkspaceSection');
        const data = window.MathBankQaData;
        if (!section || !data || section.dataset.initialized === 'true') return;
        section.dataset.initialized = 'true';
        const search = document.getElementById('qaSearchInput');
        const categories = document.getElementById('qaCategories');
        const list = document.getElementById('qaQuestionList');
        const resultCount = document.getElementById('qaResultCount');
        const empty = document.getElementById('qaEmptyState');
        let selectedCategory = '';
        const normalize = value => String(value).normalize('NFKC').toLowerCase().trim();

        document.getElementById('qaSourceNote').textContent =
            `整理自 ${data.period.start} 至 ${data.period.end} 的群聊答复，合并重复问题并核对版本变化。点击问题展开答案。`;

        const rows = data.entries.map(entry => {
            const details = document.createElement('details');
            details.className = 'qa-question';
            details.id = 'qa-' + entry.id;
            const summary = document.createElement('summary');
            summary.textContent = entry.question;
            const answer = document.createElement('div');
            answer.className = 'qa-answer';
            entry.answer.forEach(text => {
                const paragraph = document.createElement('p');
                paragraph.textContent = text;
                answer.appendChild(paragraph);
            });
            if (entry.currentNote) {
                const note = document.createElement('p');
                note.textContent = '当前代码核对：' + entry.currentNote;
                answer.appendChild(note);
            }
            const source = document.createElement('p');
            source.className = 'qa-entry-source';
            const dates = [...new Set(entry.sources.map(item => item.date))].sort();
            source.textContent = `${entry.category} · 群主答复整理 · ${dates.join(' / ')}${entry.currentNote ? ' · 已按当前版本核对' : ''}`;
            answer.appendChild(source);
            details.append(summary, answer);
            list.appendChild(details);
            return {
                entry, details,
                searchText: normalize([entry.question, entry.category, ...entry.answer, entry.currentNote || '', ...entry.keywords].join(' '))
            };
        });

        function applyFilters() {
            const tokens = normalize(search.value).split(/\s+/).filter(Boolean);
            let count = 0;
            rows.forEach(({entry, details, searchText}) => {
                const visible = (!selectedCategory || entry.category === selectedCategory) &&
                    tokens.every(token => searchText.includes(token));
                details.hidden = !visible;
                if (visible) count++;
            });
            categories.querySelectorAll('button').forEach(button => {
                button.setAttribute('aria-pressed', String(button.dataset.category === selectedCategory));
            });
            resultCount.textContent = `共 ${rows.length} 条问答，显示 ${count} 条`;
            empty.hidden = count > 0;
        }

        ['', ...new Set(data.entries.map(entry => entry.category))].forEach(category => {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.category = category;
            button.textContent = category || '全部';
            button.setAttribute('aria-pressed', String(category === selectedCategory));
            button.addEventListener('click', () => {
                selectedCategory = category;
                applyFilters();
            });
            categories.appendChild(button);
        });
        search.addEventListener('input', applyFilters);
        document.getElementById('qaResetFilters').addEventListener('click', () => {
            search.value = '';
            selectedCategory = '';
            applyFilters();
            search.focus();
        });
        applyFilters();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initQaWorkspace, {once: true});
    } else {
        initQaWorkspace();
    }
})();
