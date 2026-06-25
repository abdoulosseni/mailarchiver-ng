# Build context = racine du repo (voir docker-compose.yml)

# --- Étape 1 : build du SPA Vue avec Vite ---
FROM node:20-alpine AS build
WORKDIR /app
COPY services/web/package.json ./
RUN npm install
COPY services/web/ ./
RUN npm run build

# --- Étape 2 : service statique Nginx + proxy API ---
FROM nginx:1.27-alpine
COPY services/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
