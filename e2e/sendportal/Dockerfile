# Sendportal: the repo has no Dockerfile; this follows its install guide (PHP 8.3+, composer install, .env, key:generate, vendor:publish, migrate) on MySQL.
FROM php:8.4-cli
# sandbox only: trust the test environment's HTTPS proxy CA so downloads work
COPY --from=ca ca-bundle.crt /usr/local/share/ca-certificates/ccr-ca.crt
RUN update-ca-certificates
ADD --chmod=0755 https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/
RUN apt-get update && apt-get install -y git unzip default-mysql-client && install-php-extensions pdo_mysql zip intl bcmath gd pcntl
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
WORKDIR /app
COPY . .
RUN composer install --no-dev --no-interaction --no-progress
COPY --from=e2e start.sh /start.sh
CMD ["sh", "/start.sh"]
