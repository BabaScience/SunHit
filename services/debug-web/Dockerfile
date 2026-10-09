# Build context: repository root. Development only: Vite dev server proxying /v1 to the api.
FROM node:22.11-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json tsconfig.base.json ./
COPY libs/contracts/package.json libs/contracts/
COPY services/api/package.json services/api/
COPY services/debug-web/package.json services/debug-web/
RUN npm ci
COPY libs/contracts libs/contracts
RUN npm run build -w @sunhit/contracts
COPY services/debug-web services/debug-web
WORKDIR /app/services/debug-web
EXPOSE 5173
CMD ["npx", "vite", "--host", "0.0.0.0"]
