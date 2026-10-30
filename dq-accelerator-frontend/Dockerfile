# DQ Accelerator -- web front end
#
# Stage 1 builds the React app. Stage 2 serves it with Caddy, which also
# handles HTTPS and forwards /api to the backend container. The app and the
# API therefore share one origin: the session cookie is first-party and no
# CORS setup is needed. See the Caddyfile beside this file.
FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# VITE_API_BASE_URL is deliberately left unset: the app then calls /api on
# its own origin, which Caddy forwards to the backend. Setting it to "" would
# break every request (client.js only falls back to /api when it is absent).
RUN npm run build


FROM caddy:2.8-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
