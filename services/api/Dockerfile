# Build context: repository root.
FROM node:22.11-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.base.json ./
COPY libs/contracts/package.json libs/contracts/
COPY services/api/package.json services/api/
COPY services/debug-web/package.json services/debug-web/
RUN npm ci
COPY libs/contracts libs/contracts
COPY services/api services/api
RUN npm run build -w @sunhit/contracts && npm run build -w @sunhit/api \
 && npm prune --omit=dev --workspace @sunhit/api --workspace @sunhit/contracts

FROM node:22.11-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app/node_modules node_modules
COPY --from=build /app/libs/contracts/package.json libs/contracts/package.json
COPY --from=build /app/libs/contracts/dist libs/contracts/dist
COPY --from=build /app/libs/contracts/schemas libs/contracts/schemas
COPY --from=build /app/services/api/package.json services/api/package.json
COPY --from=build /app/services/api/dist services/api/dist
COPY db/migrations db/migrations
WORKDIR /app/services/api
USER node
EXPOSE 3000
CMD ["node", "dist/server.js"]
