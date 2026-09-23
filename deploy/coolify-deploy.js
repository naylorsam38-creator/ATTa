#!/usr/bin/env node
'use strict';
// Last link of the chain: hand a verified deploy bundle to Coolify and start it.
//
//   node deploy/coolify-deploy.js NNN [--dry-run] [--force]
//
// Gate: refuses unless results/NNN-name/deploy.json says the bundle passed its docker compose
// verification (use --force to override). Then creates a Coolify application with the
// "dockercompose" build pack from a git repo holding the bundle, points your domain at the
// `proxy` service (the capability proxy), and deploys it.
//
// Needs (environment):
//   COOLIFY_URL            e.g. https://coolify.example.com   (API is $COOLIFY_URL/api/v1)
//   COOLIFY_TOKEN          Keys & Tokens → API tokens
//   COOLIFY_PROJECT_UUID   project to create the app in
//   COOLIFY_SERVER_UUID    server to deploy to
//   COOLIFY_ENVIRONMENT    environment name (default: production)
//   GIT_REPOSITORY         public git repo URL that contains the bundle (contents of dist/NNN-name/)
//   GIT_BRANCH             branch (default: main)
//   BASE_DIRECTORY         folder of the bundle inside that repo (default: /)
//   DOMAIN                 public URL for the proxy, e.g. https://sendportal.example.com
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const args = process.argv.slice(2);
const num = args.find((a) => /^\d{1,3}$/.test(a) || /^\d{3}-/.test(a));
const dry = args.includes('--dry-run');
const force = args.includes('--force');

function fail(msg) { console.error(msg); process.exit(1); }

if (!num) fail('usage: node deploy/coolify-deploy.js NNN [--dry-run] [--force]');
const id = fs.readdirSync(path.join(ROOT, 'apps')).find((d) => d === num || d.startsWith(String(num).padStart(3, '0') + '-'));
if (!id) fail(`no numbered app ${num}`);

let verified = null;
try { verified = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', id, 'deploy.json'), 'utf8')); } catch (_) {}
if (!verified) fail(`${id}: no deploy verification yet — run: node atta.js package ${id.slice(0, 3)}`);
if (verified.status !== 'pass' && !force) {
  fail(`${id}: deploy bundle verification is ${verified.status.toUpperCase()} (${verified.detail}). Fix it or pass --force.`);
}

const env = (k, d) => process.env[k] || d;
const need = ['COOLIFY_URL', 'COOLIFY_TOKEN', 'COOLIFY_PROJECT_UUID', 'COOLIFY_SERVER_UUID', 'GIT_REPOSITORY', 'DOMAIN'];
const missing = need.filter((k) => !process.env[k]);
if (missing.length && !dry) fail(`missing environment: ${missing.join(', ')}`);

const body = {
  name: id,
  description: `ATTa ${id}: app behind the capability proxy (Port + mover + sticky notes + skins)`,
  project_uuid: env('COOLIFY_PROJECT_UUID', '<project-uuid>'),
  server_uuid: env('COOLIFY_SERVER_UUID', '<server-uuid>'),
  environment_name: env('COOLIFY_ENVIRONMENT', 'production'),
  git_repository: env('GIT_REPOSITORY', '<git-repository>'),
  git_branch: env('GIT_BRANCH', 'main'),
  build_pack: 'dockercompose',
  base_directory: env('BASE_DIRECTORY', '/'),
  docker_compose_location: '/docker-compose.yml',
  docker_compose_domains: [{ name: 'proxy', domain: env('DOMAIN', 'https://<your-domain>') }],
  instant_deploy: true,
};

async function main() {
  const api = env('COOLIFY_URL', 'https://<coolify>').replace(/\/$/, '') + '/api/v1';
  if (dry) {
    console.log(`POST ${api}/applications/public`);
    console.log(JSON.stringify(body, null, 2));
    return;
  }
  const res = await fetch(`${api}/applications/public`, {
    method: 'POST',
    headers: { authorization: `Bearer ${process.env.COOLIFY_TOKEN}`, 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) fail(`Coolify refused (${res.status}): ${text}`);
  const app = JSON.parse(text);
  console.log(`${id}: created in Coolify as ${app.uuid}; deployment started (instant_deploy).`);
  console.log(`Domain for the capability proxy: ${body.docker_compose_domains[0].domain}`);
  console.log('Set the SERVICE_PASSWORD_* / app variables named in docker-compose.yml in Coolify if you want your own values.');
}

main().catch((e) => fail(e.message));
