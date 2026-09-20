FROM node:20-slim AS build
WORKDIR /app
COPY package*.json .
# --legacy-peer-deps: openapi-typescript declares a peer range of
# typescript ^5.x that doesn't yet list this project's (newer,
# compatible in practice) typescript version -- not a real conflict.
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
