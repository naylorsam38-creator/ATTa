#!/bin/sh
# Keep one APP_KEY per deployment (generated once into the storage volume unless APP_KEY is set), migrate, serve.
set -e
cd /var/www/html
if [ -z "$APP_KEY" ]; then
  [ -f storage/app/.app-key ] || php -r 'echo "base64:".base64_encode(random_bytes(32));' > storage/app/.app-key
  export APP_KEY="$(cat storage/app/.app-key)"
fi
chown -R www-data:www-data storage bootstrap/cache
php artisan migrate --force
exec apache2-foreground
