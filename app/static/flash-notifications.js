document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-success-flash]').forEach((notice) => {
        let removeTimer;
        const close = () => {
            if (notice.classList.contains('is-closing')) return;
            window.clearTimeout(removeTimer);
            notice.classList.add('is-closing');
            window.setTimeout(() => notice.remove(), 200);
        };

        notice.querySelector('.success-flash-close')?.addEventListener('click', close);
        removeTimer = window.setTimeout(close, 5000);
    });
});
