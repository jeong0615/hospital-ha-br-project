# node:20-alpine3.22 amd64 manifest digest resolved for reproducible builds.
FROM node:20-alpine3.22@sha256:8182324b1c8f1b225f78c847b31adcffc3e4f496952c172b8993472d06e49be7 AS dependencies

WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

FROM node:20-alpine3.22@sha256:8182324b1c8f1b225f78c847b31adcffc3e4f496952c172b8993472d06e49be7 AS runtime

ENV NODE_ENV=production
ENV PORT=3000
WORKDIR /app

# Upgrade only the Alpine packages with a published Trivy fix.
RUN apk add --no-cache --upgrade libcrypto3 libssl3 zlib

COPY --from=dependencies /app/node_modules ./node_modules
COPY . ./

# npm is needed only in the install stage; keep the runtime image Node-only.
RUN rm -rf /usr/local/lib/node_modules/npm \
    /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm \
    /usr/local/bin/npx \
    /usr/local/bin/corepack

USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "require('http').get('http://127.0.0.1:3000/', r => process.exit(r.statusCode >= 200 && r.statusCode < 400 ? 0 : 1)).on('error', () => process.exit(1))"]
CMD ["node", "server.js"]
