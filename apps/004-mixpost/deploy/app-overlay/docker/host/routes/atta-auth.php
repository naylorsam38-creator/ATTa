
// Minimal email/password login for the Mixpost host app (Mixpost Lite uses the host's auth guard).
// Redirects are relative (path only): behind a proxy the Host the app sees is not the public one.
$attaGo = function (string $to) {
    $r = new \Illuminate\Http\RedirectResponse((parse_url($to, PHP_URL_PATH) ?: '/') . (($q = parse_url($to, PHP_URL_QUERY)) ? "?$q" : ''));
    $r->setSession(app('session.store'));
    return $r;
};
Route::middleware('web')->group(function () use ($attaGo) {
    Route::get('/', fn () => $attaGo('/mixpost'));
    Route::get('/login', fn () => view('auth.login'))->name('login');
    Route::post('/login', function (\Illuminate\Http\Request $request) use ($attaGo) {
        $credentials = $request->validate(['email' => 'required|email', 'password' => 'required']);
        if (\Illuminate\Support\Facades\Auth::attempt($credentials, $request->boolean('remember'))) {
            $request->session()->regenerate();
            return $attaGo($request->session()->pull('url.intended', '/mixpost'));
        }
        return $attaGo('/login')->withErrors(['email' => 'These credentials do not match our records.'])->withInput($request->only('email'));
    })->middleware('throttle:10,1');
    Route::post('/logout', function (\Illuminate\Http\Request $request) use ($attaGo) {
        \Illuminate\Support\Facades\Auth::logout();
        $request->session()->invalidate();
        $request->session()->regenerateToken();
        return $attaGo('/login');
    })->name('logout');
});
