<?php
// A separate OWNER token (write) used only by the tester to create Coolify resources (the owner's setup job).
// ATTa itself only ever gets the read+deploy token.
use App\Models\User; use App\Models\Team;
session(['currentTeam' => Team::find(0)]);
$t = User::find(0)->createToken('owner-setup', ['read','write','deploy']);
file_put_contents('/tmp/owner-token', $t->plainTextToken);
echo "owner token abilities=".json_encode($t->accessToken->abilities)."\n";
