FROM node:22-alpine

WORKDIR /srv

# Dependencies first, so editing a component does not reinstall node_modules.
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173
# The dev server, deliberately: ten more screens are coming and hot reload is
# worth more than a production bundle right now. `npm run build` emits dist/
# whenever you want the static version.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
