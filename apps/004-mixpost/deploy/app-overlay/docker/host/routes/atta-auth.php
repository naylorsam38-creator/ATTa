
// Minimal email/password login for the Mixpost host app (Mixpost Lite uses the host's auth guard).
Route::middleware('web')->group(function () {
    Route::get('/', fn () => redirect('/mixpost'));
    Route::get('/login', fn () => view('auth.login'))->name('login');
    Route::post('/login', function (\Illuminate\Http\Request $request) {
        $credentials = $request->validate(['email' => 'required|email', 'password' => 'required']);
        if (\Illuminate\Support\Facades\Auth::attempt($credentials, $request->boolean('remember'))) {
            $request->session()->regenerate();
            return redirect()->intended('/mixpost');
        }
        return back()->withErrors(['email' => 'These credentials do not match our records.'])->onlyInput('email');
    })->middleware('throttle:10,1');
    Route::post('/logout', function (\Illuminate\Http\Request $request) {
        \Illuminate\Support\Facades\Auth::logout();
        $request->session()->invalidate();
        $request->session()->regenerateToken();
        return redirect('/login');
    })->name('logout');
});
