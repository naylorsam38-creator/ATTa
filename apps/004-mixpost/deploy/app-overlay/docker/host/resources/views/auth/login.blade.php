<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · Mixpost</title>
  <style>
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f4f4f8; font: 15px/1.5 system-ui, sans-serif; color: #1f1f2e; }
    form { width: min(360px, 92vw); background: #fff; padding: 28px; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.08); }
    h1 { font-size: 20px; margin: 0 0 18px; }
    label { display: block; font-weight: 600; margin: 12px 0 4px; }
    input[type=email], input[type=password] { width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #cfcfe0; border-radius: 8px; font: inherit; }
    .row { display: flex; align-items: center; gap: 8px; margin: 14px 0; }
    button { width: 100%; padding: 11px; border: 0; border-radius: 8px; background: #4f46e5; color: #fff; font-weight: 600; cursor: pointer; }
    .error { color: #b42318; margin: 8px 0 0; }
  </style>
</head>
<body>
  <form method="POST" action="/login">
    @csrf
    <h1>Sign in to Mixpost</h1>
    <label for="email">Email</label>
    <input id="email" name="email" type="email" value="{{ old('email') }}" required autofocus autocomplete="username">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" required autocomplete="current-password">
    <div class="row"><input id="remember" name="remember" type="checkbox"><label for="remember" style="margin:0;font-weight:400">Remember me</label></div>
    @error('email')<p class="error">{{ $message }}</p>@enderror
    <button type="submit">Sign in</button>
  </form>
</body>
</html>
