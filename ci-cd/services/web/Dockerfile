# node:20-alpine3.22 amd64 manifest digest resolved for reproducible builds.
FROM node:20-alpine3.22@sha256:8182324b1c8f1b225f78c847b31adcffc3e4f496952c172b8993472d06e49be7 AS dependencies

WORKDIR /app
COPY package*.json ./
RUN apk add --no-cache --virtual .build-deps python3 make g++ \
    && npm ci --omit=dev \
    && npm cache clean --force

# Keep the runtime on the same official Node major while using the smaller,
# separately patched Alpine base. Build-only native-module tooling remains in
# the discarded dependencies stage.
FROM node:20-alpine3.22@sha256:8182324b1c8f1b225f78c847b31adcffc3e4f496952c172b8993472d06e49be7 AS runtime

ENV NODE_ENV=production
ENV PORT=3000
WORKDIR /app

# bcrypt's native module links against the C++ runtime. Keep only that runtime
# dependency in the final image; compilers and headers exist only in the build stage.
# Upgrade only the Alpine packages with a published Trivy fix.
RUN apk add --no-cache --upgrade libstdc++ libcrypto3 libssl3 zlib

COPY --from=dependencies /app/node_modules ./node_modules
COPY app.js package.json ./
COPY public ./public
COPY views ./views

# The host process uses loopback for PM2. Containers must listen on the
# container interface so the Compose port mapping can reach the app.
RUN sed -i "s/app\.listen(PORT, '127\.0\.0\.1'/app.listen(PORT, '0.0.0.0'/" app.js

# npm is needed only in the install stage; keep the runtime image Node-only.
RUN rm -rf /usr/local/lib/node_modules/npm \
    /usr/local/lib/node_modules/corepack \
    /usr/local/bin/npm \
    /usr/local/bin/npx \
    /usr/local/bin/corepack

USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "require('http').get('http://127.0.0.1:3000/health', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"]
CMD ["node", "app.js"]
