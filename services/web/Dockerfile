FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build && npm run build:storybook
FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# The design-system workshop ships with the site (see src/design-system/README.md).
COPY --from=build /app/storybook-static /usr/share/nginx/html/storybook
