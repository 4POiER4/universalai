(() => {
  'use strict';
  const form = document.querySelector('#login-form');
  const providerInput = document.querySelector('#provider');
  const note = document.querySelector('#provider-note');
  const errorBox = document.querySelector('#auth-error');
  const submit = document.querySelector('#login-submit');

  document.querySelectorAll('.provider-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.provider-tab').forEach((item) => {
        item.classList.toggle('active', item === tab);
      });
      providerInput.value = tab.dataset.provider;
      note.textContent = tab.dataset.provider === 'ldap'
        ? 'Пароль проверяется напрямую в Active Directory и не сохраняется платформой.'
        : 'Локальные профили предназначены для администратора и тестирования без AD.';
      errorBox.textContent = '';
      document.querySelector('#username').focus();
    });
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    errorBox.textContent = '';
    submit.disabled = true;
    submit.textContent = 'Входим…';
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          provider: providerInput.value,
          username: document.querySelector('#username').value,
          password: document.querySelector('#password').value,
          next: form.dataset.next || '/messenger',
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Не удалось выполнить вход');
      window.location.assign(data.next || '/messenger');
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      submit.disabled = false;
      submit.textContent = 'Войти';
    }
  });
})();
