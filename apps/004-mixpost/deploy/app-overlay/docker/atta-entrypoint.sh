#!/bin/sh
# One APP_KEY per deployment (kept in the storage volume unless APP_KEY is set), migrate,
# create/update the admin from MIXPOST_ADMIN_EMAIL / MIXPOST_ADMIN_PASSWORD, serve.
set -e
cd /var/www/html
if [ -z "$APP_KEY" ]; then
  [ -f storage/app/.app-key ] || php -r 'echo "base64:".base64_encode(random_bytes(32));' > storage/app/.app-key
  export APP_KEY="$(cat storage/app/.app-key)"
fi
chown -R www-data:www-data storage bootstrap/cache
php artisan migrate --force
if [ -n "$MIXPOST_ADMIN_EMAIL" ] && [ -n "$MIXPOST_ADMIN_PASSWORD" ]; then
  php artisan tinker --execute="App\Models\User::updateOrCreate(['email'=>getenv('MIXPOST_ADMIN_EMAIL')],['name'=>'Admin','password'=>bcrypt(getenv('MIXPOST_ADMIN_PASSWORD'))]);"
fi
exec apache2-foreground
