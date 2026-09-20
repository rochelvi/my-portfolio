# Публикация

Схема: Cloudflare (DNS + TLS для посетителя) → nginx на VPS (TLS для origin,
порт 443) → контейнер на `127.0.0.1:8080`.

Контейнер наружу не смотрит: в `docker-compose.yml` порт привязан к loopback,
поэтому достучаться до него можно только с самой машины.

## 1. Подготовка VPS

Нужен Docker с плагином compose. На Ubuntu/Debian:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # перелогиниться, чтобы группа применилась
```

Проверка: `docker compose version`.

## 2. Первый запуск

```bash
sudo mkdir -p /srv && cd /srv
git clone git@github.com:rochelvi/my-portfolio.git portfolio
cd portfolio
docker compose up -d --build
curl -I http://127.0.0.1:8080/        # ожидаем 200
```

## 3. Домен в Cloudflare

В DNS добавить запись на IP сервера, прокси включён (оранжевое облако):

| Тип | Имя | Значение | Proxy |
| --- | --- | --- | --- |
| A | `@` | IP вашего VPS | Proxied |
| CNAME | `www` | ваш домен | Proxied |

В **SSL/TLS → Overview** выставить режим **Full (strict)**. Режим `Flexible`
не использовать: Cloudflare пойдёт на сервер по голому HTTP, и запрос поедет
по интернету в открытом виде, хотя в браузере будет замок.

## 4. Сертификат для origin

Проще всего взять **Origin Certificate** у самой Cloudflare: он живёт 15 лет и
не требует ни продления, ни доступа к серверу извне (что важно, раз трафик
идёт через прокси и ACME-проверка по HTTP не пройдёт).

**SSL/TLS → Origin Server → Create Certificate**, дальше на сервере:

```bash
sudo mkdir -p /etc/ssl/cloudflare
sudo nano /etc/ssl/cloudflare/origin.pem   # вставить сертификат
sudo nano /etc/ssl/cloudflare/origin.key   # вставить приватный ключ
sudo chmod 600 /etc/ssl/cloudflare/origin.key
```

Альтернатива — Let's Encrypt через certbot с DNS-01 проверкой. Обычный HTTP-01
за включённым прокси Cloudflare не работает.

## 5. nginx на хосте

```bash
sudo apt install -y nginx
sudo nano /etc/nginx/sites-available/portfolio
```

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name ВАШ_ДОМЕН www.ВАШ_ДОМЕН;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ВАШ_ДОМЕН www.ВАШ_ДОМЕН;

    ssl_certificate     /etc/ssl/cloudflare/origin.pem;
    ssl_certificate_key /etc/ssl/cloudflare/origin.key;

    # health-пробу наружу не пускаем: она нужна только самому контейнеру
    location = /healthz { return 404; }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/portfolio /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Закрыть прямой доступ

Раз трафик приходит только через Cloudflare, наружу достаточно 80 и 443, и то
лишь с их адресов. Минимум:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```

Строже — пускать на 443 только [диапазоны Cloudflare](https://www.cloudflare.com/ips/),
тогда origin нельзя будет открыть по IP в обход прокси.

## 7. Обновление сайта

```bash
cd /srv/portfolio
git pull
docker compose up -d --build
```

Сборка занимает секунды: меняется только слой с `index.html`, базовый образ уже
в кэше. Старый контейнер останавливается после того, как собран новый.

Если ловите старую версию страницы — это кэш Cloudflare. Сбросить:
**Caching → Configuration → Purge Everything**. Сам контейнер отдаёт HTML с
`Cache-Control: no-cache`, так что после сброса всё встанет на место.

## 8. Откат

```bash
cd /srv/portfolio
git log --oneline -5
git checkout <нужный-коммит>
docker compose up -d --build
```

## Проверка после выката

```bash
curl -I https://ВАШ_ДОМЕН/                       # 200, Content-Encoding: gzip
curl -I https://ВАШ_ДОМЕН/assets/Daniil_Mishin_CV.pdf   # 200, application/pdf
docker compose ps                                # состояние healthy
docker compose logs --tail=50
```
