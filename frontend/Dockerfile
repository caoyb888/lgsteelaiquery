# ============================================================
# 莱钢 AI 问数 — 前端 Dockerfile
# 多阶段构建：development / production
# ============================================================

# ---- 构建阶段 ----
FROM node:20-alpine AS builder

WORKDIR /app

# 先复制 package 文件，利用 Docker 层缓存
COPY package.json pnpm-lock.yaml* package-lock.json* yarn.lock* ./

# 优先使用 pnpm；若无锁文件则降级 npm
RUN if [ -f pnpm-lock.yaml ]; then \
        npm install -g pnpm && pnpm install --frozen-lockfile; \
    elif [ -f package-lock.json ]; then \
        npm ci; \
    else \
        npm install; \
    fi

COPY . .

RUN npm run build

# ---- 生产阶段（Nginx 托管静态资源）----
FROM nginx:1.25-alpine AS production

# 删除默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 复制前端构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 前端 SPA 路由支持（所有路由回退到 index.html）
COPY nginx-spa.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
