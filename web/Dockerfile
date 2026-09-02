FROM node:22-slim AS base
WORKDIR /app
RUN corepack enable
# pnpm's dependency-status check tries to interactively confirm before
# touching node_modules, which has no TTY to prompt in a Docker build
# (ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY) -- CI=true is pnpm's own
# documented fix for exactly this, surfaced directly in its own error
# message. Verified by actually building this image, not assumed.
ENV CI=true

FROM base AS deps
COPY package.json pnpm-lock.yaml* pnpm-workspace.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install

FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM base AS run
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["pnpm", "start"]
