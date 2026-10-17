# 8. Sendportal — commit c76a9cffa11f (2026-10-12)
Steps: A ok · B **FAIL** · C–J NOT REACHED - blocked by step B

## App faults
1. **B — No Docker setup and no official image.** Repository has no Dockerfile / compose file / .dockerignore; the only "docker" string is a CI lint action (`.github/workflows/format.yml:21 uses: docker://oskarstark/php-cs-fixer-ga:3.13.0`). README.md:20-24 points to https://sendportal.io/docs/v4/getting-started/installation, which documents only bare-metal install (PHP ≥ 8.3, Composer, MySQL/PostgreSQL, Apache/nginx) — 0 mentions of Docker in the installation, configuration-and-setup and quickstart pages; no official image named. Per test rules the app cannot be run in Docker. See search-for-docker.txt.

- C. Configure — NOT REACHED - blocked by step B
- D. Build — NOT REACHED - blocked by step B
- E. Start — NOT REACHED - blocked by step B
- F. HTTP — NOT REACHED - blocked by step B
- G. Playwright pages/signup/login — NOT REACHED - blocked by step B
- H. Forms — NOT REACHED - blocked by step B
- I. Browser console — NOT REACHED - blocked by step B
- J. Container logs — NOT REACHED - blocked by step B

Fault count: 1 · first failing step: B
