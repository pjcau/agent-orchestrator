FROM node:25-alpine

WORKDIR /app

COPY docs/website/package.json docs/website/package-lock.json ./
RUN npm ci

COPY docs/website/ .

EXPOSE 3000

CMD ["npm", "start", "--", "--host", "0.0.0.0", "--port", "3000"]
