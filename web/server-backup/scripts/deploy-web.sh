#!/usr/bin/env bash
# 새 복구 대상 디렉터리에 소스 준비만 수행. 현재 운영 서버에서 실행하지 말 것.
set -euo pipefail
if [[ $# -ne 1 || "$1" != /* ]]; then
  echo '사용법: deploy-web.sh /새로운/절대/대상경로' >&2; exit 2
fi
web_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
target=$(realpath -m -- "$1")
case "$target" in
  /home/vboxuser/hospital-web|/home/vboxuser/hospital-web/*|"$web_root"|"$web_root"/*|/etc|/etc/*|/usr|/usr/*|/var|/var/*|/)
    echo '운영·백업·시스템 경로는 대상이 될 수 없습니다.' >&2; exit 2;;
esac
if [[ -e "$target" || -L "$target" ]]; then echo '기존 경로에는 쓰지 않습니다.' >&2; exit 2; fi
umask 077
mkdir -- "$target"
cp -R -- "$web_root/rootfs/home/vboxuser/hospital-web/." "$target/"
cp -- "$web_root/dr/environment.example" "$target/.env.example"
printf '%s
' '소스 준비 완료. 새 대상에서 Secret 입력, npm ci --omit=dev 및 서비스 설정을 별도 수행하세요.'
