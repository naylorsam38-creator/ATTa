# Build offline from the vendored npm cache (.npm-cache), then serve the static build with nginx.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
COPY .npm-cache ./.npm-cache
RUN npm ci --offline --cache ./.npm-cache --no-audit --no-fund
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
