
// ATTa test shim: Mixpost Lite relies on the host app's auth. Log in the seeded
// test user and send a *relative* redirect so the browser stays on whatever origin it used.
Route::get('/login', function () {
    \Illuminate\Support\Facades\Auth::login(\App\Models\User::where('email', 'atta@example.com')->firstOrFail());
    request()->session()->regenerate();
    return response('', 302, ['Location' => '/mixpost']);
})->name('login');
