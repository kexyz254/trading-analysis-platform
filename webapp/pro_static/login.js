(() => {
  const password = document.getElementById('password');
  const button = document.getElementById('login');
  const error = document.getElementById('error');

  async function authenticate() {
    error.textContent = '';
    button.disabled = true;

    try {
      const response = await fetch('/api/pro/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: password.value})
      });

      password.value = '';

      if (!response.ok) {
        error.textContent =
          response.status === 429
            ? 'Too many failed attempts. Try again later.'
            : 'Authentication failed.';
        return;
      }

      location.replace('/pro/manual');
    } catch (_) {
      error.textContent = 'Authentication service unavailable.';
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener('click', authenticate);

  password.addEventListener('keydown', event => {
    if (event.key === 'Enter') authenticate();
  });
})();
