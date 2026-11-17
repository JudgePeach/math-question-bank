(function () {
    'use strict';

    const workspaceIds = ['dashboard', 'bank', 'import', 'paper', 'records', 'qa'];

    function closeWorkspaceDropdown() {
        const menu = document.getElementById('workspaceDropdownMenu');
        if (!menu) return;
        menu.classList.add('scale-90', 'opacity-0', 'pointer-events-none');
        menu.classList.remove('scale-100', 'opacity-100');
        document.getElementById('workspaceDropdownBtn')?.setAttribute('aria-expanded', 'false');
        document.getElementById('workspaceDropdownArrow')?.classList.remove('rotate-180');
        document.removeEventListener('click', closeWorkspaceDropdown);
    }

    window.toggleWorkspaceDropdown = function (event) {
        event.stopPropagation();
        const menu = document.getElementById('workspaceDropdownMenu');
        if (!menu) return;
        if (!menu.classList.contains('pointer-events-none')) {
            closeWorkspaceDropdown();
            return;
        }
        closeThemeDropdown();
        menu.classList.remove('scale-90', 'opacity-0', 'pointer-events-none');
        menu.classList.add('scale-100', 'opacity-100');
        document.getElementById('workspaceDropdownBtn')?.setAttribute('aria-expanded', 'true');
        document.getElementById('workspaceDropdownArrow')?.classList.add('rotate-180');
        document.addEventListener('click', closeWorkspaceDropdown);
    };

    function updateNavigation(workspaceId, workspaceName) {
        document.querySelectorAll('[data-app-nav-target]').forEach(button => {
            if (button.dataset.appNavTarget === workspaceId) {
                button.setAttribute('aria-current', 'page');
            } else {
                button.removeAttribute('aria-current');
            }
        });
        const title = document.getElementById('currentWorkspaceName');
        if (title) title.textContent = workspaceName;

        for (const id of workspaceIds) {
            document.getElementById(`ws-check-${id}`)?.classList.toggle('hidden', id !== workspaceId);
            document.getElementById(`ws-btn-${id}`)?.classList.toggle('font-medium', id === workspaceId);
        }
        closeWorkspaceDropdown();
    }

    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (workspaceId === 'qa' && window.__qaEnabled === false) return;

        document.documentElement.classList.remove('init-ws-dashboard', 'init-ws-paper');
        const previousWorkspace = document.body.dataset.workspace || 'dashboard';
        if (workspaceId !== previousWorkspace) {
            if (previousWorkspace === 'bank') window.closeBankFilterPopover?.(false);
            if (previousWorkspace === 'paper') window.closePaperFilterPopover?.(false);
            if (workspaceId === 'import') {
                const section = document.getElementById('importWorkspaceSection');
                if (section) section.dataset.returnNavTarget = previousWorkspace;
            }
        }

        updateNavigation(workspaceId, workspaceName);
        document.body.dataset.workspace = workspaceId;
        try {
            localStorage.setItem('mathbank_active_workspace', workspaceId);
            if (window.__serverInstanceId) {
                localStorage.setItem('mathbank_server_instance_id', window.__serverInstanceId);
            }
        } catch (error) {
            // Navigation also works when the browser blocks local storage.
        }

        const target = workspaceIds.includes(workspaceId) ? workspaceId : 'bank';
        if (target === 'bank') window.closeBankDetail?.();
        for (const id of workspaceIds) {
            document.getElementById(`${id}WorkspaceSection`)?.classList.toggle('hidden', id !== target);
        }
        if (target === 'dashboard') window.loadDashboardData?.();
        if (target === 'paper') window.renderPaperWorkspace?.();
    };

    // Place the import center beside the other workspaces in the shared shell.
    const mainWorkspaceContainer = document.querySelector('#appContentShell > main');
    const importWorkspaceSection = document.getElementById('importWorkspaceSection');
    const paperWorkspaceSection = document.getElementById('paperWorkspaceSection');
    if (mainWorkspaceContainer && importWorkspaceSection && paperWorkspaceSection
        && importWorkspaceSection.parentElement !== mainWorkspaceContainer) {
        mainWorkspaceContainer.insertBefore(importWorkspaceSection, paperWorkspaceSection);
    }

    document.addEventListener('DOMContentLoaded', () => {
        // Reloading returns home. The feature scripts restore their own data.
        window.selectWorkspace('dashboard', '工作台');
    }, { once: true });
})();
