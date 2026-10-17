# Medusa: repo has no Dockerfile; follows the docs' Docker install (medusa-starter-default, node 20, yarn, migrate, then `medusa develop`).
FROM node:20
COPY --from=ca ca-bundle.crt /ccr-ca.crt
ENV NODE_EXTRA_CA_CERTS=/ccr-ca.crt
WORKDIR /server
COPY . .
RUN corepack enable && yarn install
EXPOSE 9000
CMD ["sh", "-c", "yarn medusa db:migrate && (yarn medusa user -e admin@e2e-test.org -p E2e-Pass-123! || true) && yarn dev"]
