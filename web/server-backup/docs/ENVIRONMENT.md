# 환경변수 사용 위치

실제 `.env` 값은 수록하지 않습니다. `.env.example`은 소스에서 참조된 이름만 포함합니다. `ZABBIX_USER/PASSWORD`는 미마운트 모듈 전용으로 주석 처리했습니다.

- `DB_HOST`: web/rootfs/home/vboxuser/hospital-web/test-db.js:7, web/rootfs/home/vboxuser/hospital-web/test-db.js:14, web/rootfs/home/vboxuser/hospital-web/app.js:36, web/rootfs/home/vboxuser/hospital-web/routes/check.js:13, web/rootfs/home/vboxuser/hospital-web/routes/check.js:26, web/rootfs/home/vboxuser/hospital-web/routes/check.js:35
- `DB_NAME`: web/rootfs/home/vboxuser/hospital-web/test-db.js:10, web/rootfs/home/vboxuser/hospital-web/test-db.js:18, web/rootfs/home/vboxuser/hospital-web/app.js:40, web/rootfs/home/vboxuser/hospital-web/routes/check.js:17, web/rootfs/home/vboxuser/hospital-web/routes/check.js:28
- `DB_PASSWORD`: web/rootfs/home/vboxuser/hospital-web/test-db.js:17, web/rootfs/home/vboxuser/hospital-web/app.js:39, web/rootfs/home/vboxuser/hospital-web/routes/check.js:16
- `DB_PORT`: web/rootfs/home/vboxuser/hospital-web/test-db.js:8, web/rootfs/home/vboxuser/hospital-web/test-db.js:15, web/rootfs/home/vboxuser/hospital-web/app.js:37, web/rootfs/home/vboxuser/hospital-web/routes/check.js:14, web/rootfs/home/vboxuser/hospital-web/routes/check.js:27, web/rootfs/home/vboxuser/hospital-web/routes/check.js:36
- `DB_USER`: web/rootfs/home/vboxuser/hospital-web/test-db.js:9, web/rootfs/home/vboxuser/hospital-web/test-db.js:16, web/rootfs/home/vboxuser/hospital-web/app.js:38, web/rootfs/home/vboxuser/hospital-web/routes/check.js:15
- `PORT`: web/rootfs/home/vboxuser/hospital-web/app.js:14
- `SERVER_ROLE`: web/rootfs/home/vboxuser/hospital-web/app.js:752, web/rootfs/home/vboxuser/hospital-web/app.js:758, web/rootfs/home/vboxuser/hospital-web/app.js:765, web/rootfs/home/vboxuser/hospital-web/app.js:771
- `SESSION_SECRET`: web/rootfs/home/vboxuser/hospital-web/app.js:160
- `ZABBIX_PASSWORD`: web/rootfs/home/vboxuser/hospital-web/routes/check.js:59
- `ZABBIX_URL`: web/rootfs/home/vboxuser/hospital-web/app.js:493, web/rootfs/home/vboxuser/hospital-web/routes/check.js:48
- `ZABBIX_USER`: web/rootfs/home/vboxuser/hospital-web/routes/check.js:58
