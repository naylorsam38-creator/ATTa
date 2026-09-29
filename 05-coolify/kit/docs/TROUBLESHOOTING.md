# Troubleshooting

Start with `./scripts/verify.sh`. It names the failing piece and usually the fix.
The logs are:

- Installer: `/data/coolify/source/installation-*.log`
- Upgrades: `/data/coolify/source/upgrade-*.log`
- Containers: `docker logs coolify` (also `coolify-db`, `coolify-redis`, `coolify-realtime`, `coolify-proxy`)

---

### Dashboard never becomes healthy; `docker logs coolify` shows `socket() [::]:8080 failed (97: Address family not supported by protocol)`

IPv6 is disabled in the kernel (`ipv6.disable=1`).
Coolify's built-in web server listens on IPv6 as well as IPv4 and can't start without it.
Preflight blocks this before install, but it can also happen if someone disables IPv6 later.
(We hit this during testing.)

Fix it properly by re-enabling kernel IPv6 support. You don't need to configure any IPv6 addresses:

```bash
grep -n ipv6.disable /etc/default/grub         # find it
sed -i 's/ *ipv6.disable=1//' /etc/default/grub
update-grub && reboot
ls /proc/net/if_inet6                           # exists = fixed
```

To disable IPv6 *networking* for hardening, use `sysctl net.ipv6.conf.all.disable_ipv6=1` instead.
That leaves kernel support in place and Coolify works.

### "Coolify would not create the admin account" / "Public sign-up is ENABLED"

Coolify creates the admin account once, when it first boots.
If its checks fail at that moment, it logs an error and carries on **with public sign-up open**.
The kit validates the same rules up front and re-runs Coolify's seeder after install (`ensure_admin_account`), so this should only appear if Coolify keeps rejecting the details.

See Coolify's reason:

```bash
docker logs coolify 2>&1 | grep -A5 'Root User'
```

| Coolify says | Cause | Fix |
|---|---|---|
| `The email field must be a valid email address.` | The email domain doesn't accept mail according to DNS: no MX/A/AAAA record, a "null MX" (for example `example.com`), a reserved name (`.test`, `.local`, `.internal`, `.lan`…), or DNS failed inside the container | Use a real mailbox domain. Check DNS from the container: `docker exec coolify php -r 'var_dump(checkdnsrr("gmail.com","MX"));'` |
| `The password has appeared in a data leak` | The password is in the breach corpus | Leave `ROOT_USER_PASSWORD` empty to generate one |
| `The username format is invalid` | Unsupported characters | Letters, digits, `_`, `-` only |

The values live in `/data/coolify/source/.env` (`ROOT_USERNAME`, `ROOT_USER_EMAIL`, `ROOT_USER_PASSWORD`).
Fix them there. Then recreate the container so it picks them up, and re-run the kit:

```bash
cd /data/coolify/source
LATEST_IMAGE=$(grep ^LATEST_IMAGE= .env | cut -d= -f2) docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate coolify
/opt/atta/scripts/install.sh
```

**Until it's fixed, anyone who can reach port 8000 can register and become admin.**
Firewall port 8000 to your own IP in the meantime.
If you'd rather claim the instance by hand: open `/register`, sign up (the first user becomes admin), then **Settings → Configuration → turn off Registration**.

### Preflight: "Port 80/443 is already in use"

Another web server (nginx, apache, caddy) is running.
Coolify's proxy needs these ports.

```bash
ss -ltnp 'sport = :80'
systemctl disable --now nginx     # or apache2 / caddy
```

### Preflight: "Cannot reach Coolify CDN / Docker Hub"

The server has no outbound internet, or DNS is broken.
Check `curl -I https://cdn.coollabs.io/coolify/versions.json` and `cat /etc/resolv.conf`.
Some providers need outbound rules enabled in their firewall.

### Image pulls fail with `429 Too Many Requests`

Docker Hub rate-limits anonymous pulls per IP, and shared or NAT IPs hit the limit fast.
Choose one:

- `docker login` with a free Docker Hub account (higher limit), then re-run the installer.
- Set `REGISTRY_URL=ghcr.io` in `config/coolify.env` so Coolify's own images come from GitHub's registry instead.

### The installer changed `/etc/docker/daemon.json`

If `daemon.json` has no `default-address-pools`, Coolify's installer **replaces** the file with its own settings: log rotation plus a 10.0.0.0/8 address pool.
It saves the original as `/etc/docker/daemon.json.original-<date>`.
If you had custom settings (registry mirrors, insecure registries, data-root), merge them back in:

```bash
ls /etc/docker/daemon.json.original-*
# edit /etc/docker/daemon.json to include your settings AND keep default-address-pools
systemctl restart docker
```

Once a pool is present, later installer runs leave the file alone.

### 10.0.0.0/8 clashes with my VPC or VPN

Set `DOCKER_ADDRESS_POOL_BASE` (for example `172.30.0.0/16`) and `DOCKER_ADDRESS_POOL_SIZE` (for example `24`) in `config/coolify.env` **before** the first install.

### Let's Encrypt certificate doesn't appear

- `dig +short coolify.yourdomain.com` must return this server's IP.
- Port 80 must be open in the cloud firewall. Let's Encrypt validates over HTTP.
- Check `docker logs coolify-proxy` for `acme` errors. Let's Encrypt rate-limits repeated failures, so fix DNS first, then retry.

### Reverse proxy disappears right after the server is validated

Coolify can start its proxy twice at once: the validation job's start, plus its once-a-minute server check that finds no proxy container yet.
Each start removes and recreates `coolify-proxy`, so they can race and leave no proxy at all.
Coolify's logs then show `No such container` and the proxy status reads `exited`.
(We hit this during testing.)
`install.sh` only finishes once the proxy has stayed up for 75 seconds, and restarts it if needed.
If it happens later, click **Servers → localhost → Proxy → Restart**, or re-run `./scripts/install.sh`.

### `/data/coolify/source/.env` has mode 644 after an upgrade

That's expected: Coolify's upgrade rewrites the file with default permissions.
The secrets are protected by `/data/coolify` being `700`, which `verify.sh` checks by actually trying to read the file as an unprivileged user.
Don't `chmod 600` the file: the Coolify container reads it as a different user (uid 9999) and would lose access.

### Forgot the admin password

```bash
docker exec -it coolify php artisan root:reset-password
```

### verify.sh: "Coolify's localhost server has no SSH user"

Coolify's installer takes the SSH login user from the `$USER` environment variable.
Under cloud-init, cron or some `sudo` setups, `$USER` is empty, and Coolify then can't deploy anything, even though every container is healthy.
(We hit this during testing.)
The kit's `install.sh` always sets it, so this only affects installs done another way.

Fix: in Coolify, open **Servers → localhost**, set **User** to `root`, save, then click **Validate Server**.

### Coolify can't connect to "localhost" server

Coolify manages its own host over SSH to `host.docker.internal:22` as root, using a key in `/data/coolify/ssh/keys`. Check that:

- `sshd` is running: `systemctl status ssh`.
- `/root/.ssh/authorized_keys` contains a line ending in `coolify`.
- `PermitRootLogin` is `prohibit-password` or `yes`, not `no`: `sshd -T | grep permitrootlogin`.

After `restore.sh`, the restored key is authorised automatically.

### Disk full

```bash
df -h /
docker system df
docker system prune -a      # removes stopped containers and unused images
```

Then enable automatic cleanup in Coolify: **Servers → localhost → Docker Cleanup**.

### Restore says "Backup is from Coolify X but this server runs Y"

Restore requires matching versions so the database schema lines up.
Set `COOLIFY_VERSION=X` in `config/coolify.env`, run `install.sh`, restore, then `upgrade.sh Y`.
