(() => {
  const telegram = window.Telegram?.WebApp;
  const toast = document.querySelector('.toast');

  if (telegram) {
    telegram.ready();
    telegram.expand();
    document.documentElement.style.setProperty('--telegram-bg', telegram.backgroundColor || '#0877b9');
  }

  document.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.action;
      if (telegram?.sendData) {
        telegram.sendData(JSON.stringify({ action }));
        telegram.close();
        return;
      }
      toast.textContent = 'این منو باید از داخل Telegram باز شود.';
    });
  });
})();
