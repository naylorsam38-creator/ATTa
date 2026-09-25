# Runbook: from nothing to ATT running on Coolify

Work through the steps in order.
Every command runs on the server as `root` unless it says otherwise.

---

## 1. Get a server

| | Minimum | Recommended for ATT |
|---|---|---|
| OS | Ubuntu 22.04 / 24.04, Debian 12 | **Ubuntu 24.04 LTS** |
| CPU | 2 vCPU | 2-4 vCPU |
| RAM | 2 GB | **4 GB+** (apps are built on this box) |
| Disk | 30 GB | **40-80 GB SSD** (Docker images add up) |
| Arch | x86_64 or ARM64 | either |

Good options include Hetzner (CPX21 / CAX21), DigitalOcean (4 GB droplet), Vultr and Linode.
Pick a region close to ATT's users.

Before you continue, check two things:

- **Kernel IPv6 must be on.** It doesn't need to be configured or routed; it just can't be *disabled*. Most providers leave it on. `ls /proc/net/if_inet6` should exist, and preflight checks this for you.
- **Don't install Docker from snap.** Let the installer install Docker.

## 2. Cloud firewall

Set this up in your provider's dashboard: the Hetzner or DigitalOcean Firewall, or an AWS Security Group.
Don't rely on `ufw` alone: Docker publishes ports around it.

| Port | Why | Keep open? |
|---|---|---|
| 22/tcp | SSH | Yes. Restrict to your IP if you can |
| 80/tcp | HTTP and Let's Encrypt challenges | Yes |
| 443/tcp | HTTPS for ATT and the dashboard | Yes |
| 8000/tcp | Dashboard before it has a domain | **Close after step 7** |
| 6001/tcp | Realtime before the dashboard has a domain | **Close after step 7** |
| 6002/tcp | Web terminal before the dashboard has a domain | **Close after step 7** |

## 3. DNS

At your DNS provider, create A records (and AAAA if the server has IPv6) pointing at the server's public IP:

| Record | Example | Used for |
|---|---|---|
| `coolify` | `coolify.yourdomain.com` | The Coolify dashboard |
| `att` (or `@`) | `att.yourdomain.com` | ATT itself |

DNS can take a few minutes to propagate. Check with `dig +short coolify.yourdomain.com`.

## 4. Install

```bash
apt-get update && apt-get install -y git
git clone https://github.com/naylorsam38-creator/ATTa.git /opt/atta
cd /opt/atta
cp config/coolify.env.example config/coolify.env
nano config/coolify.env
```

In `config/coolify.env`:

- `ROOT_USERNAME`: your login name, for example `sam`. No spaces.
- `ROOT_USER_EMAIL`: a real address. Coolify checks that the domain has DNS.
- `ROOT_USER_PASSWORD`: **leave empty** and a strong password is generated for you.
- `AUTOUPDATE`: `true` (default) gets security fixes automatically. `false` keeps the pinned version until you run `scripts/upgrade.sh`.

Then run:

```bash
./scripts/install.sh --dry-run    # validation + preflight only
./scripts/install.sh              # the real install, 3-10 minutes
```

A successful run ends with `Coolify 4.3.23 is installed and verified.`
If anything fails, the message tells you what to fix, and it's safe to re-run.
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## 5. Save your secrets (do this now)

```bash
cat /root/coolify-admin-credentials.txt    # your dashboard login
cat /data/coolify/source/.env              # Coolify's own secrets, including APP_KEY
```

Put both into a password manager, then delete the credentials file:

```bash
shred -u /root/coolify-admin-credentials.txt
```

If the server dies, `APP_KEY` is the only way to decrypt the secrets stored in a backup.

## 6. First login

1. Open `http://<server-ip>:8000` and log in with the saved credentials.
2. The onboarding wizard asks where to deploy. Choose **This Machine (localhost)**.
   The kit has already validated it and started the reverse proxy, so this step just confirms.
3. **Profile → Two-factor authentication**: turn it on.

## 7. Dashboard domain and HTTPS

1. **Settings → Configuration → Instance's Domain**: enter `https://coolify.yourdomain.com` and save.
2. Wait about 30 seconds, then open `https://coolify.yourdomain.com`. You should see a valid padlock (Let's Encrypt).
3. Once that works, **remove ports 8000, 6001 and 6002** from the cloud firewall. The dashboard, realtime updates and web terminal now go through 443.
4. Re-run `./scripts/verify.sh` to confirm everything is still healthy.

If the certificate doesn't appear, the DNS record isn't pointing at the server yet or port 80 is blocked.

## 8. Backups

Set up two layers:

**Coolify's own state** (projects, settings, encrypted secrets): run the kit's backup nightly and copy it off the server.

```bash
./scripts/backup.sh     # test it once by hand
echo '17 3 * * * root /opt/atta/scripts/backup.sh >>/var/log/coolify-backup.log 2>&1' > /etc/cron.d/coolify-backup
```

Backups land in `/root/coolify-backups/` (the 14 newest are kept).
**Copy them off the server regularly**, for example with `rclone` to S3, Backblaze B2 or Dropbox.
A backup that dies with the server isn't a backup.

**ATT's data**: for any database you create in Coolify for ATT, open the database, go to **Backups**, add a schedule and an S3 destination (**Settings → S3 Storages**).

To restore onto a new server:

1. Install the same Coolify version on it with steps 1-4.
2. Copy the backup archive onto it.
3. Run `./scripts/restore.sh /path/to/coolify-backup-*.tar.gz`.
4. Log in with the **original** admin credentials.
5. Point the DNS records from step 3 at the new server's IP. Apps come back as you redeploy them (or on their next push).

## 9. Deploy ATT

See [DEPLOY-ATT.md](DEPLOY-ATT.md).

## 10. Maintenance

| Task | How |
|---|---|
| Health check | `./scripts/verify.sh` |
| Upgrade (when AUTOUPDATE=false) | `./scripts/upgrade.sh <version>`. It takes a backup first. [Releases](https://github.com/coollabsio/coolify/releases) |
| Disk filling up | Coolify **Servers → localhost → Docker Cleanup**: enable scheduled cleanup. Or run `docker system prune -a` (removes unused images) |
| Update the kit | `cd /opt/atta && git pull` |
| OS security updates | `apt-get update && apt-get upgrade`, or enable `unattended-upgrades` |
| Locked out of the dashboard | `docker exec -it coolify php artisan root:reset-password` |
