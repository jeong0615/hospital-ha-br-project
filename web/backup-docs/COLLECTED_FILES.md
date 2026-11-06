# 수집 파일 목록

원본→복사본 매핑입니다. symlink는 내용만 일반 파일로 보존했으며 Nginx include 대상과 비활성을 구분했습니다. 조회 산출물은 이 디렉터리와 영역별 README에 있습니다.

| 원본 | 복사본 |
|---|---|
| `/home/vboxuser/hospital-web/test-db.js` | `web/rootfs/home/vboxuser/hospital-web/test-db.js` |
| `/home/vboxuser/hospital-web/package.json` | `web/rootfs/home/vboxuser/hospital-web/package.json` |
| `/home/vboxuser/hospital-web/app.js` | `web/rootfs/home/vboxuser/hospital-web/app.js` |
| `/home/vboxuser/hospital-web/package-lock.json` | `web/rootfs/home/vboxuser/hospital-web/package-lock.json` |
| `/home/vboxuser/hospital-web/routes/check.js` | `web/rootfs/home/vboxuser/hospital-web/routes/check.js` |
| `/home/vboxuser/hospital-web/views/index.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/index.ejs` |
| `/home/vboxuser/hospital-web/views/reservations/check.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/reservations/check.ejs` |
| `/home/vboxuser/hospital-web/views/reservations/new.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/reservations/new.ejs` |
| `/home/vboxuser/hospital-web/views/reservations/list.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/reservations/list.ejs` |
| `/home/vboxuser/hospital-web/views/community/notices.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/community/notices.ejs` |
| `/home/vboxuser/hospital-web/views/community/index.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/community/index.ejs` |
| `/home/vboxuser/hospital-web/views/community/faq.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/community/faq.ejs` |
| `/home/vboxuser/hospital-web/views/partials/header.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/partials/header.ejs` |
| `/home/vboxuser/hospital-web/views/partials/footer.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/partials/footer.ejs` |
| `/home/vboxuser/hospital-web/views/health/list.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/health/list.ejs` |
| `/home/vboxuser/hospital-web/views/pages/detail.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/pages/detail.ejs` |
| `/home/vboxuser/hospital-web/views/pages/not-found.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/pages/not-found.ejs` |
| `/home/vboxuser/hospital-web/views/pages/list.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/pages/list.ejs` |
| `/home/vboxuser/hospital-web/views/auth/login.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/auth/login.ejs` |
| `/home/vboxuser/hospital-web/views/auth/signup.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/auth/signup.ejs` |
| `/home/vboxuser/hospital-web/views/auth/mypage.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/auth/mypage.ejs` |
| `/home/vboxuser/hospital-web/views/auth/forbidden.ejs` | `web/rootfs/home/vboxuser/hospital-web/views/auth/forbidden.ejs` |
| `/home/vboxuser/hospital-web/public/404.html` | `web/rootfs/home/vboxuser/hospital-web/public/404.html` |
| `/home/vboxuser/hospital-web/public/js/menu.js` | `web/rootfs/home/vboxuser/hospital-web/public/js/menu.js` |
| `/home/vboxuser/hospital-web/public/css/style.css` | `web/rootfs/home/vboxuser/hospital-web/public/css/style.css` |
| `/etc/nginx/nginx.conf` | `web/rootfs/etc/nginx/nginx.conf` |
| `/etc/nginx/mime.types` | `web/rootfs/etc/nginx/mime.types` |
| `/etc/nginx/sites-enabled/hospital` | `web/rootfs/etc/nginx/active/sites-enabled/hospital` |
| `/etc/nginx/sites-enabled/hospital.bak_20260625_123126` | `web/rootfs/etc/nginx/active/sites-enabled/hospital.bak_20260625_123126` |
| `/etc/nginx/modules-enabled/50-mod-http-geoip2.conf` | `web/rootfs/etc/nginx/active/modules-enabled/50-mod-http-geoip2.conf` |
| `/etc/nginx/modules-enabled/50-mod-http-image-filter.conf` | `web/rootfs/etc/nginx/active/modules-enabled/50-mod-http-image-filter.conf` |
| `/etc/nginx/modules-enabled/50-mod-http-xslt-filter.conf` | `web/rootfs/etc/nginx/active/modules-enabled/50-mod-http-xslt-filter.conf` |
| `/etc/nginx/modules-enabled/50-mod-mail.conf` | `web/rootfs/etc/nginx/active/modules-enabled/50-mod-mail.conf` |
| `/etc/nginx/modules-enabled/50-mod-stream.conf` | `web/rootfs/etc/nginx/active/modules-enabled/50-mod-stream.conf` |
| `/etc/nginx/modules-enabled/70-mod-stream-geoip2.conf` | `web/rootfs/etc/nginx/active/modules-enabled/70-mod-stream-geoip2.conf` |
| `/etc/nginx/sites-available/default` | `web/rootfs/etc/nginx/inactive/default` |
| `/etc/cloudflared/config.yml` | `web/rootfs/etc/cloudflared/config.yml.example` |
| `/etc/zabbix/zabbix_agentd.conf` | `web/rootfs/etc/zabbix/zabbix_agentd.conf` |
| `/etc/systemd/system/hospital-web.service` | `web/rootfs/etc/systemd/system/hospital-web.service` |
| `/etc/systemd/system/cloudflared.service` | `web/rootfs/etc/systemd/system/cloudflared.service` |
| `/lib/systemd/system/zabbix-agent.service` | `web/rootfs/etc/systemd/system/zabbix-agent.service` |
| `/lib/systemd/system/nginx.service` | `web/rootfs/etc/systemd/system/nginx.service` |
