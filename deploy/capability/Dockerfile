# Capability proxy image: ui-bridge/proxy.js + capability-port (port.js, caps/*.mjs) + this app's layouts/skins.
# The proxy is the public entrypoint; it forwards to the app and injects /_cp/port.js before </head>.
FROM node:22-alpine
WORKDIR /cp
COPY --chown=node:node ui-bridge ./ui-bridge
COPY --chown=node:node capability-port ./capability-port
ENV APP_ID=app TARGET=http://app:80 PORT=8080
EXPOSE 8080
USER node
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:${PORT}/_cp/port.js || exit 1
CMD ["sh", "-c", "exec node ui-bridge/proxy.js --app-id \"$APP_ID\" --target \"$TARGET\" --host 0.0.0.0 --port \"$PORT\""]
