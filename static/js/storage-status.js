/* Optional local storage notice; no user data or filesystem paths are sent. */
(function () {
    'use strict';
    var notice;
    async function refreshStorageNotice() {
        try {
            var response = await fetch('/api/storage/status', {cache: 'no-store'});
            if (!response.ok) return;
            var status = await response.json();
            var message = '';
            if (status.error) message = '外置资料索引暂不可用，自动迁出已停止；请检查存储记录。';
            else if (status.enabled && !status.external_connected) {
                message = '资料盘未连接：新文件会暂存内盘；已外置且没有本地缓存的图片、原文档需连接数据盘后读取。';
            } else if (status.enabled && status.last_error) {
                message = '部分资料尚未完成迁出，内盘原件已保留；请检查存储记录。';
            }
            if (!notice && message) {
                notice = document.createElement('span');
                notice.setAttribute('role', 'status');
                notice.style.cssText = 'display:inline-block;max-width:11em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:4px 8px;border-radius:6px;background:#fff4cd;color:#634b00;font-size:12px;flex-shrink:1';
                var host = document.getElementById('headerUtilityActions');
                if (!host) return;
                host.prepend(notice);
            }
            if (notice) {
                notice.textContent = !message ? '' : status.error ? '资料存储需检查' : !status.external_connected ? '资料盘未连接 · 新文件暂存内盘' : '资料迁出待检查';
                notice.title = message;
                notice.setAttribute('aria-label', message);
                notice.hidden = !message;
            }
        } catch (_error) { /* Existing connectivity handling owns service loss. */ }
    }
    refreshStorageNotice();
    window.setInterval(refreshStorageNotice, 60000);
}());
