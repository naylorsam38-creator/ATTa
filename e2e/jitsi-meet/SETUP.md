# Jitsi Meet: official docker-jitsi-meet (run from /tmp/claude-0/run/jitsi): env.example -> .env, gen-passwords.sh, config dirs. Changed: ports 9120/9140, PUBLIC_URL = proxy address, ENABLE_IPV6=0 (sandbox has no IPv6), image tag stable.
- config dirs chowned to uid 1000 (containers run as uid 1000; sandbox shell is root)
