set -e
cp .env.example .env
sed -i "s#^APP_URL=.*#APP_URL=http://127.0.0.1:9208#; s#^DB_HOST=.*#DB_HOST=mysql#; s#^DB_DATABASE=.*#DB_DATABASE=sendportal#; s#^DB_USERNAME=.*#DB_USERNAME=sendportal#; s#^DB_PASSWORD=.*#DB_PASSWORD=sendportal-pass#; s#^SENDPORTAL_REGISTER=.*#SENDPORTAL_REGISTER=true#; s#^APP_DEBUG=.*#APP_DEBUG=false#" .env
php artisan key:generate --force
php artisan vendor:publish --provider='Sendportal\Base\SendportalBaseServiceProvider' --force
until php artisan migrate --force; do sleep 3; done
exec php artisan serve --host=0.0.0.0 --port=8000
