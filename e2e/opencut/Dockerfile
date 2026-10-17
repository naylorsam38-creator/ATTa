# OpenCut: the repo has no Dockerfile; this follows its README (bun, pinned 1.3.11 in .prototools): build apps/web, serve with vite preview.
FROM oven/bun:1.3.11
# sandbox only: trust the test environment's HTTPS proxy CA so package downloads work
COPY --from=ca ca-bundle.crt /etc/ssl/certs/ccr-ca.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ccr-ca.crt SSL_CERT_FILE=/etc/ssl/certs/ccr-ca.crt
WORKDIR /src
COPY . .
WORKDIR /src/apps/web
RUN bun install && bun run build
EXPOSE 5173
CMD ["bun", "x", "vite", "preview", "--port", "5173", "--host", "0.0.0.0", "--strictPort"]
