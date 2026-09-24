#!/usr/bin/env python3
"""
runtimes.py — reusable ways to run an app that ships its source but no way to start it
(no compose file, no published image, no Dockerfile).

Each runtime is a TYPE of app, recognised from the app's own files, never a named app:
  php-composer   any PHP app with composer.json (Laravel apps get Laravel's standard setup steps)
  rust-wasm-web  a Rust workspace that compiles to WebAssembly and ships a Vite web frontend

For a matching app the runner is handed a generated Dockerfile + compose file (written to the
runner's work folder, never into the library). It then goes through exactly the same start,
adapt-and-retry and six-stage qualification as every other way of running an app, and is only
saved as the app's recipe if it passes. Nothing here is specific to Flarum, Krayin or Graphite:
another app of the same type gets the same treatment.
"""
from __future__ import annotations
import json, re
from pathlib import Path

# ============================ RULES / CONFIG — edit here, nothing below needs reading ============================
# PHP version for PHP apps whose composer.json doesn't ask for one. Raise when apps start needing newer.
PHP_DEFAULT = "8.3"
# PHP extensions installed for every PHP app (the common set Laravel/Flarum-style apps need).
PHP_EXTENSIONS = "pdo_mysql mysqli zip gd intl bcmath exif opcache"
# Database given to PHP apps that use one (their .env.example / composer.json says mysql/mariadb).
PHP_DB_IMAGE = "mariadb:11"
# Node version used to build web frontends (Rust + wasm apps). LTS.
NODE_IMAGE = "node:22-bookworm"
# Rust toolchain image for Rust + wasm apps.
RUST_IMAGE = "rust:1-bookworm"
# Binaryen (wasm-opt) release used when an app doesn't pin one in its own files.
BINARYEN_DEFAULT = "130"
# Web server that serves a built static frontend.
STATIC_SERVER_IMAGE = "nginx:1.27-alpine"
# ==============================================================================================================


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ detection
def detect(d: Path) -> dict | None:
    """Which reusable runtime fits this app, from its own files. None = no runtime fits."""
    for fn in (_php, _rust_wasm_web):
        r = fn(d)
        if r:
            return r
    return None


def _php(d: Path) -> dict | None:
    comp = _json(d / "composer.json")
    if not comp:
        return None
    req = comp.get("require") or {}
    m = re.search(r"(\d+\.\d+)", str(req.get("php", "")))
    php = m.group(1) if m and float(m.group(1)) >= 8.1 else PHP_DEFAULT
    # Document root: the shallowest folder holding the front controller.
    docroot = next((c for c in ("public", "upload", "web", "htdocs", ".") if (d / c / "index.php").is_file()), None)
    if docroot is None:
        return None
    laravel = (d / "artisan").is_file()
    env_example = next((n for n in (".env.example", ".env.dist") if (d / n).is_file()), None)
    # Needs a database: its settings example says so, it requires a database library, or its own README
    # names MySQL/MariaDB as a requirement. Evidence from the app's files only, never its name.
    readme = " ".join(_read(p) for p in d.glob("README*"))
    uses_db = bool(re.search(r"DB_CONNECTION=(mysql|mariadb)", _read(d / env_example) if env_example else "")) \
        or any(re.match(r"(laravel/framework|illuminate/database|doctrine/(dbal|orm)|ext-pdo_mysql|ext-mysqli)$", k) for k in req) \
        or bool(re.search(r"\b(MySQL|MariaDB)\b", readme))
    return {"kind": "runtime", "runtime": "php-composer", "php": php, "docroot": docroot, "laravel": laravel,
            "env_example": env_example, "database": uses_db,
            "why": f"reusable PHP runtime (PHP {php}, web root {docroot}{', Laravel' if laravel else ''}"
                   f"{', with database' if uses_db else ''}): the app ships no way to start it"}


def _rust_wasm_web(d: Path) -> dict | None:
    lock = _read(d / "Cargo.lock")
    if not (d / "Cargo.toml").is_file() or 'name = "wasm-bindgen"' not in lock:
        return None
    front = next((f for f in ("frontend", "web", "app", "ui") if (d / f / "package.json").is_file()
                  and any((d / f / v).is_file() for v in ("vite.config.ts", "vite.config.js", "vite.config.mjs"))), None)
    if not front:
        return None
    m = re.search(r'name = "wasm-bindgen"\nversion = "([^"]+)"', lock)
    wb = m.group(1) if m else None
    # Pinned tool versions, read from the app's own build files when it pins them.
    tools = _read(d / "tools" / "cargo-run" / "src" / "requirements" / "wasm_opt.rs")
    bm = re.search(r'VERSION: &str = "(\d+)"', tools)
    about = re.search(r"cargo-about@([\d.]+)", _read(d / "tools" / "cargo-run" / "src" / "requirements.rs"))
    # The app's own documented build entry: a cargo-run build tool (`cargo run build`), else wasm-pack + vite.
    own_builder = (d / "tools" / "cargo-run").is_dir()
    return {"kind": "runtime", "runtime": "rust-wasm-web", "frontend": front, "wasm_bindgen": wb,
            "binaryen": bm.group(1) if bm else BINARYEN_DEFAULT, "cargo_about": about.group(1) if about else None,
            "build": "cargo run build" if own_builder else "wasm-pack build --target web && npm --prefix {front} run build",
            "why": f"reusable Rust+WebAssembly web runtime (frontend {front}, wasm-bindgen {wb}): built from source, served static"}


# ------------------------------------------------------------------ generated files
def write(part: dict, d: Path, out: Path) -> Path:
    """Write the Dockerfile + compose for a runtime part into `out` (the runner's work folder).
    Returns the compose file. Build context is the app's library folder, read-only to the build."""
    out.mkdir(parents=True, exist_ok=True)
    rt = part["runtime"]
    if rt == "php-composer":
        (out / "Dockerfile").write_text(_php_dockerfile(part))
        compose = _php_compose(part, d, out)
    elif rt == "rust-wasm-web":
        (out / "Dockerfile").write_text(_rust_dockerfile(part))
        compose = {"services": {"app": {"build": {"context": str(d), "dockerfile": str(out / "Dockerfile")},
                                        "ports": ["8080:80"]}}}
    else:
        raise ValueError(f"unknown runtime {rt}")
    f = out / "compose.runtime.json"
    f.write_text(json.dumps(compose, indent=1))
    return f


def _php_dockerfile(p: dict) -> str:
    docroot = "/var/www/html" + ("" if p["docroot"] == "." else "/" + p["docroot"])
    laravel = p["laravel"]
    return f"""FROM php:{p['php']}-apache
# Downloads retry on their own: a connection that drops mid-build is retried in place, not a failed build.
RUN printf 'Acquire::Retries "5";\\n' > /etc/apt/apt.conf.d/80-atta-retries
ENV COMPOSER_PROCESS_TIMEOUT=900
RUN apt-get update && apt-get install -y --no-install-recommends git unzip libzip-dev libpng-dev libjpeg-dev \\
      libfreetype6-dev libicu-dev libonig-dev default-mysql-client && rm -rf /var/lib/apt/lists/* \\
 && docker-php-ext-configure gd --with-freetype --with-jpeg \\
 && docker-php-ext-install -j"$(nproc)" {PHP_EXTENSIONS} \\
 && a2enmod rewrite \\
 && sed -ri 's!/var/www/html!{docroot}!g' /etc/apache2/sites-available/*.conf \\
 && printf '<Directory {docroot}>\\n AllowOverride All\\n Require all granted\\n</Directory>\\n' > /etc/apache2/conf-enabled/atta-docroot.conf
COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
WORKDIR /var/www/html
COPY . /var/www/html
RUN rm -rf .ui-capability .atta-intake.json .git \\
 && COMPOSER_ALLOW_SUPERUSER=1 composer install --no-dev --no-interaction --prefer-dist --optimize-autoloader \\
 && chown -R www-data:www-data /var/www/html
RUN printf '%s\\n' '#!/bin/sh' 'set -e' \\
  '{_php_start(p)}' \\
  'exec apache2-foreground' > /usr/local/bin/atta-start.sh && chmod +x /usr/local/bin/atta-start.sh
CMD ["/usr/local/bin/atta-start.sh"]
"""


def _php_start(p: dict) -> str:
    """Startup steps: wait for the database, then the framework's STANDARD first-run steps only."""
    steps = []
    if p["database"]:
        steps.append('for i in $(seq 1 60); do mysqladmin ping -h"$DB_HOST" -u"$DB_USERNAME" -p"$DB_PASSWORD" --silent && break; sleep 2; done')
    if p["laravel"]:
        if p.get("env_example"):
            steps.append(f'[ -f .env ] || cp {p["env_example"]} .env')
        steps.append("php artisan key:generate --force || true")
        steps.append("php artisan migrate --force || true")
        steps.append("php artisan storage:link || true")
        steps.append("chown -R www-data:www-data storage bootstrap/cache")
    return "' '".join(s.replace("'", "'\\''") for s in steps) or "true"


def _php_compose(p: dict, d: Path, out: Path) -> dict:
    app = {"build": {"context": str(d), "dockerfile": str(out / "Dockerfile")}, "ports": ["8080:80"]}
    svc = {"app": app}
    if p["database"]:
        env = {"DB_CONNECTION": "mysql", "DB_HOST": "db", "DB_PORT": "3306", "DB_DATABASE": "app",
               "DB_USERNAME": "app", "DB_PASSWORD": "${ATTA_DB_PASSWORD}", "APP_URL": "http://localhost"}
        app["environment"] = env
        app["depends_on"] = ["db"]
        svc["db"] = {"image": PHP_DB_IMAGE, "environment": {
            "MARIADB_DATABASE": "app", "MARIADB_USER": "app", "MARIADB_PASSWORD": "${ATTA_DB_PASSWORD}",
            "MARIADB_ROOT_PASSWORD": "${ATTA_DB_PASSWORD}"}}
    return {"services": svc}


def _rust_dockerfile(p: dict) -> str:
    front = p["frontend"]
    wb = f"cargo install -f wasm-bindgen-cli@{p['wasm_bindgen']}" if p.get("wasm_bindgen") else "true"
    about = f"cargo install --features=cli -f cargo-about@{p['cargo_about']}" if p.get("cargo_about") else "true"
    build = p["build"].replace("{front}", front)
    return f"""FROM {NODE_IMAGE} AS node
FROM {RUST_IMAGE} AS build
COPY --from=node /usr/local /usr/local
# Downloads retry on their own (cargo crates, npm packages, the binaryen release): a connection that
# drops mid-build is retried in place instead of failing the build. The release is saved to a file
# first: a retried download piped straight into tar could hand it a corrupt stream.
ENV CARGO_NET_RETRY=10 CARGO_HTTP_TIMEOUT=120 npm_config_fetch_retries=5 npm_config_fetch_retry_mintimeout=10000 \\
    npm_config_fetch_retry_maxtimeout=120000
RUN rustup target add wasm32-unknown-unknown \\
 && {wb} && {about} \\
 && curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 --connect-timeout 30 -o /tmp/binaryen.tar.gz \\
    https://github.com/WebAssembly/binaryen/releases/download/version_{p['binaryen']}/binaryen-version_{p['binaryen']}-x86_64-linux.tar.gz \\
 && tar -xzf /tmp/binaryen.tar.gz -C /usr/local --strip-components=1 && rm -f /tmp/binaryen.tar.gz
WORKDIR /src
COPY . /src
RUN rm -rf .ui-capability .atta-intake.json && {build}
FROM {STATIC_SERVER_IMAGE}
COPY --from=build /src/{front}/dist /usr/share/nginx/html
"""
