<?php
// Owner-side setup from ATTa docs/COOLIFY-HANDOFF.md, done with Coolify's own models (same as RootUserSeeder
// + Settings->Advanced "API Access" + Keys & Tokens), because the seeder's email:dns / uncompromised()
// checks need internet DNS/HIBP, which the air-gapped testbed does not have.
use App\Models\User; use App\Models\Team; use App\Models\InstanceSettings; use Illuminate\Support\Facades\Hash;
$u = User::find(0);
if (!$u) {
  $u = (new User)->forceFill(['id'=>0,'name'=>env('ROOT_USERNAME','admin'),'email'=>env('ROOT_USER_EMAIL'),'password'=>Hash::make(env('ROOT_USER_PASSWORD'))]);
  $u->save();
  $u->teams()->attach(Team::find(0), ['role'=>'owner']);
}
$s = InstanceSettings::get(); $s->is_api_enabled = true; $s->allowed_ips = null; $s->is_registration_enabled = false; $s->save();
session(['currentTeam' => Team::find(0)]);
$t = $u->createToken('atta-handoff', ['read','deploy']);
file_put_contents('/tmp/atta-token', $t->plainTextToken);
echo "user=".$u->email." team0=".($u->teams()->where('team_id',0)->exists()?'owner':'none')." api=".var_export($s->is_api_enabled,true)." token_abilities=".json_encode($t->accessToken->abilities)." team_id=".$t->accessToken->team_id."\n";
