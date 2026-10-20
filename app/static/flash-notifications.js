const initFlashNotifications = () => {
    document.querySelectorAll(
        '[data-success-flash], .flash.success, .form-flash.success, .form-message.success, .disassembly-flash.success',
    ).forEach((notice) => {
        if (notice.dataset.flashAutoDismiss === 'true') return;
        notice.dataset.flashAutoDismiss = 'true';

        let removeTimer;
        const close = () => {
            if (notice.classList.contains('is-closing')) return;
            window.clearTimeout(removeTimer);
            notice.style.transition = 'opacity 0.2s ease, transform 0.2s ease';
            notice.classList.add('is-closing');
            notice.style.opacity = '0';
            notice.style.transform = 'translateY(-0.35rem)';
            window.setTimeout(() => notice.remove(), 200);
        };

        notice.querySelector('.success-flash-close, [data-close-flash]')?.addEventListener('click', close);
        removeTimer = window.setTimeout(close, 5000);
    });
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFlashNotifications, { once: true });
} else {
    initFlashNotifications();
}
