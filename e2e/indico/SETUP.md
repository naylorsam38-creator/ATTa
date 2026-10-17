# Indico: official indico-containers/indico-prod (run from /tmp/claude-0/run/indico). Changed: SECRET_KEY set, BASE_URL = proxy address http://127.0.0.1:9219, nginx port 9119, CA cert added to worker image for the sandbox.
- nginx.conf: removed 'listen [::]:8080' (sandbox has no IPv6)
