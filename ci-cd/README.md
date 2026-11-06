# CI/CD 백업

이 디렉터리는 `hospital-ci-cd` 저장소의 `main` 브랜치(원본 커밋 `24cf1e92935f6ba5b687ec5c96122df7f39af02b`)를 2026-11-06에 파일 단위로 보관한 공개용 스냅샷이다. 원본의 디렉터리 상대 경로를 유지해 워크플로가 참조하는 코드와 설정을 함께 찾을 수 있다. 원본 저장소의 Git 이력이나 이후의 미커밋 변경사항은 포함하지 않는다.

## 구조

```text
ci-cd/
├── .github/workflows/       # 품질 검사·보안 스캔, 아티팩트 정리
├── automation/ansible/       # 배포 및 복구 자동화
├── docs/                     # 아키텍처·운영 문서
├── infra/terraform/          # 인프라 정의와 관련 스크립트
├── monitoring/               # Grafana·Zabbix·Wazuh 정의
├── operations/               # ChatOps·DR·보안·DB 운영 스크립트
├── services/                 # CI 빌드·검사 대상 Web·EMR 코드
├── tests/                    # CI 검증 테스트
├── .gitignore
├── .gitleaks.toml
└── README.md
```

워크플로 파일은 `ci-cd/.github/workflows/`에 보관한다. 공개 백업 저장소에서는 이 경로의 워크플로가 자동 실행되지 않는다. `msp/`에도 일부 CI/CD 정의와 운영 파일이 있으며, 이 디렉터리는 원래 `hospital-ci-cd` 저장소의 파일 배치를 따로 보존한다.

## 재구성

새로운 비공개 CI/CD 저장소가 필요하면 이 디렉터리의 파일을 새 저장소 루트로 복사한다. 먼저 `.github/workflows/`의 실행 조건과 권한, Ansible·Terraform·Docker 경로, 테스트 대상 파일을 검토한다. 그다음 필요한 GitHub Actions 변수·Secret, AWS OIDC/IAM, ECR, 외부 시스템 권한과 Terraform 상태를 별도로 구성한다. `ci-cd/` 안에서 `README.md`는 이 백업 설명이므로 새 저장소의 목적에 맞게 수정한다.

공개용 치환값 `AWS_ARN_REDACTED`, `AWS_RESOURCE_ID_REDACTED`, `AWS_ACCOUNT_ID_REDACTED`는 실제 환경에서 사용하기 전에 각각 유효한 설정 또는 변수 참조로 교체해야 한다. 이 스냅샷을 그대로 실행할 수 있다고 가정하지 않는다.

GitHub Actions의 과거 실행 기록, 로그, 아티팩트, 저장소 설정, Git 이력, 실제 자격증명과 Terraform state는 이 파일 백업에 포함되지 않는다. 발표·검증에 필요한 결과 아티팩트가 있다면 원본 저장소를 삭제하기 전에 별도로 선별해 보관한다. 실제 Secret 값은 공개 저장소에 추가하지 않는다.
