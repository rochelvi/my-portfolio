# syntax=docker/dockerfile:1
FROM nginx:1.30-alpine-slim

# No RUN step on purpose: with only COPY the build never starts a container,
# so it works on hosts where the docker bridge is broken. The stock
# conf.d/default.conf stays in the image but is dead weight — our nginx.conf
# replaces the whole file and never includes conf.d.
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html
# резюме и прочая статика: положите файлы в assets/ в корне репозитория
COPY assets/ /usr/share/nginx/html/assets/

# unprivileged: port 8080 needs no capabilities, pid and temp files go to /tmp
USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
