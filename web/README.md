# Web

Web Application과 Web Server 설정을 새 Ubuntu 또는 DR 환경에서 재구성하기 위한 공개 백업이다. 애플리케이션 파일과 `/etc` 설정은 실제 Web 서버 절대경로를 기준으로 `rootfs/`에 배치한다.

<br>

## 구조

```text
web/
├── README.md
├── BackUP_README.md
├── backup-docs/
├── cloudflare/
├── dr/
├── monitoring/
├── rootfs/
│   ├── etc/
│   │   ├── cloudflared/
│   │   ├── netplan/
│   │   ├── nginx/
│   │   ├── systemd/system/
│   │   └── zabbix/
│   └── home/vboxuser/hospital-web/
│       ├── app.js
│       ├── package.json
│       ├── package-lock.json
│       ├── public/
│       ├── routes/
│       └── views/
└── server-backup/
    ├── docs/
    └── scripts/
```

주요 경로 매핑은 다음과 같다.

```text
web/rootfs/home/vboxuser/hospital-web/
→ /home/vboxuser/hospital-web/

web/rootfs/etc/nginx/
→ /etc/nginx/

web/rootfs/etc/systemd/system/
→ /etc/systemd/system/
```

Cloudflared, Netplan, Zabbix 설정도 각각 `rootfs/etc/` 아래의 실제 절대경로에 대응한다. `backup-docs/`와 `server-backup/docs/`는 운영·복구 설명, `server-backup/scripts/`는 사전 점검·배포 준비·health check 스크립트다.

<br>

## 재구성 순서

1. 새 Ubuntu, Node.js/npm, Nginx와 필요한 패키지를 준비한다.
2. `rootfs/home/vboxuser/hospital-web/`의 소스를 `/home/vboxuser/hospital-web/`에 배치한다.
3. 별도 Secret 저장소에서 `.env`와 DB/Cloudflare/Zabbix 자격증명을 주입한다.
4. `package-lock.json` 기준으로 `npm ci`를 실행한다.
5. `rootfs/etc/`의 설정을 검토하고 Nginx는 `nginx -t` 후 적용한다.
6. `hospital-web.service`와 필요한 systemd unit을 검토한 뒤 서비스를 시작한다.
7. 애플리케이션, DB 연결, Reverse Proxy, 외부 HTTP/HTTPS와 MSP Monitoring을 순서대로 검증한다.

검증 스크립트는 다음 위치에 있다.

```bash
cd web/server-backup/scripts
./check-prerequisites.sh
./health-check.sh
./verify-services.sh
```

`deploy-web.sh`는 지정한 새 대상 디렉터리에 소스와 example 환경 파일만 준비하며, 기존 운영·시스템 경로에는 쓰지 않는다.

<br>

## 제외 항목

`.env`, 실제 Cloudflare credential JSON, API/DB/Slack Token, SSH/TLS Private Key, 로그, cache, `node_modules`, 업로드 데이터, DB Dump와 운영용 archive는 저장하지 않는다. Nginx의 `.bak` 또는 inactive 설정은 자동 활성화하지 않고 복구 전에 검토한다.

실제 Secret은 Example 또는 placeholder로만 표시하고, 복구 시 별도 Secret 저장소에서 주입한다.
