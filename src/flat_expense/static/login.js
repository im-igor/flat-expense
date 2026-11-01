'use strict';

const form = document.querySelector('#login-form');
const errorBox = document.querySelector('#login-error');

form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  errorBox.classList.add('hidden');
  try {
    const response = await fetch('/web/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Flat-Expense': 'login'},
      body: JSON.stringify({password: form.password.value}),
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'Слишком много попыток. Подождите несколько минут.' : 'Неверный пароль.');
    location.replace('/');
  } catch (error) {
    errorBox.textContent = error.message || 'Не удалось войти.';
    errorBox.classList.remove('hidden');
  } finally {
    button.disabled = false;
  }
});
