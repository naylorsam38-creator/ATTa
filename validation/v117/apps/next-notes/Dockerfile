# Offline build from the vendored npm cache, then run Next's standalone server.
FROM node:20-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY .npm-cache ./.npm-cache
RUN npm ci --offline --cache ./.npm-cache --no-audit --no-fund
COPY next.config.js ./
COPY app ./app
RUN npm run build
FROM node:20-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
