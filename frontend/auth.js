// Браузер проверяет поля, затем отправляет их серверу для независимой проверки.
const form = document.querySelector('#auth-form');
const passwordInput = document.querySelector('#password');
const confirmInput = document.querySelector('#confirm-password');
const confirmField = document.querySelector('#confirm-field');
const passwordHint = document.querySelector('#password-hint');
const message = document.querySelector('#form-message');
const loginButton = document.querySelector('#login-mode');
const registerButton = document.querySelector('#register-mode');
const togglePassword = document.querySelector('#toggle-password');
let mode = 'login';
let submitting = false;

// Одна форма используется для двух режимов, поэтому поля не дублируются.
function setMode(nextMode) {
  if (submitting) return;
  mode = nextMode;
  const isRegistration = mode === 'register';
  form.reset();
  confirmInput.setCustomValidity('');
  passwordInput.type = 'password';
  togglePassword.textContent = 'Показать';
  togglePassword.setAttribute('aria-pressed', 'false');
  confirmField.hidden = !isRegistration;
  confirmInput.disabled = !isRegistration;
  confirmInput.required = isRegistration;
  passwordHint.hidden = !isRegistration;
  passwordInput.minLength = isRegistration ? 8 : 1;
  passwordInput.autocomplete = isRegistration ? 'new-password' : 'current-password';
  passwordInput.placeholder = isRegistration ? 'Придумайте пароль' : 'Введите пароль';
  document.querySelector('#auth-title').textContent = isRegistration ? 'Начнём путешествие?' : 'С возвращением!';
  document.querySelector('#auth-description').textContent = isRegistration
    ? 'Создайте аккаунт, чтобы хранить поездки и собирать маршруты под себя.'
    : 'Войдите, чтобы планировать новые поездки и возвращаться к сохранённым.';
  document.querySelector('#submit-label').textContent = isRegistration ? 'Создать аккаунт' : 'Войти';
  document.title = `${isRegistration ? 'Регистрация' : 'Вход'} — TripWise`;
  loginButton.classList.toggle('selected', !isRegistration);
  registerButton.classList.toggle('selected', isRegistration);
  loginButton.setAttribute('aria-pressed', String(!isRegistration));
  registerButton.setAttribute('aria-pressed', String(isRegistration));
  message.hidden = true;
}

function validateConfirmation() {
  const mismatch = mode === 'register' && confirmInput.value !== passwordInput.value;
  confirmInput.setCustomValidity(mismatch ? 'Пароли не совпадают. Повторите тот же пароль.' : '');
}

loginButton.addEventListener('click', () => setMode('login'));
registerButton.addEventListener('click', () => setMode('register'));

togglePassword.addEventListener('click', () => {
  const show = passwordInput.type === 'password';
  passwordInput.type = show ? 'text' : 'password';
  togglePassword.textContent = show ? 'Скрыть' : 'Показать';
  togglePassword.setAttribute('aria-pressed', String(show));
});

form.addEventListener('input', () => {
  message.hidden = true;
  validateConfirmation();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting) return;
  validateConfirmation();
  if (!form.reportValidity()) return;
  if (!['http:', 'https:'].includes(window.location.protocol)) {
    message.textContent = 'Откройте TripWise через запущенный сервер: http://127.0.0.1:8765/.';
    message.hidden = false;
    return;
  }
  const payload = {
    email: document.querySelector('#email').value,
    password: passwordInput.value,
  };
  if (mode === 'register') payload.password_confirmation = confirmInput.value;
  const submitButton = document.querySelector('#submit-button');
  submitting = true;
  submitButton.disabled = true;
  loginButton.disabled = true;
  registerButton.disabled = true;
  document.querySelector('#submit-label').textContent = 'Подождите…';
  message.hidden = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/auth/${mode}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) {
      message.textContent = typeof result.detail === 'string' ? result.detail : 'Не удалось выполнить действие. Попробуйте снова.';
      message.hidden = false;
      return;
    }
    window.location.assign('/trips');
  } catch {
    message.textContent = 'Не удалось получить ответ сервера. Проверьте, запущен ли он. Если отправляли регистрацию, попробуйте затем войти с теми же данными.';
    message.hidden = false;
  } finally {
    clearTimeout(timeout);
    passwordInput.value = '';
    confirmInput.value = '';
    payload.password = '';
    if ('password_confirmation' in payload) payload.password_confirmation = '';
    confirmInput.setCustomValidity('');
    submitting = false;
    submitButton.disabled = false;
    loginButton.disabled = false;
    registerButton.disabled = false;
    document.querySelector('#submit-label').textContent = mode === 'register' ? 'Создать аккаунт' : 'Войти';
  }
});
