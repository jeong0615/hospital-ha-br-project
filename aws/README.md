# AWS (AWS DR)

이 디렉터리는 AWS DR 환경을 공개 가능한 수준에서 설명한다. `physical/`·`gns3/`처럼 구성과 연결 관계를 기록하며, 실행 가능한 Terraform·Ansible·CI/CD 소스는 기존 `msp/`와 `ci-cd/`에 보관한다. 이 문서는 계정이나 EC2 디스크를 복제한 백업이 아니다.

<br>

## 구조

```text
aws/
├── README.md       # 구성과 재구성 순서
└── inventory.md    # 민감정보를 제외한 AWS 읽기 전용 관측 결과
```

<br>

## 구성 관계

백업된 Terraform 정의에는 VPC, Internet Gateway, Public/Private Subnet, 라우팅, 보안 그룹, Web·Doc/EMR·DB EC2, Web NAT Instance, 선택적 S3 Gateway Endpoint, ECR, S3 백업 버킷, GitHub OIDC/IAM 및 비용 관리 Lambda가 포함된다. `inventory.md`에는 2026-11-06의 AWS 읽기 전용 조회로 확인한 수량과 상태를 별도로 적었다. 실제로 확인된 Endpoint는 SSM 계열 Interface Endpoint 2개이며 S3 Gateway Endpoint는 확인되지 않았다. 정의 파일의 존재와 운영 환경에서 기능이 성공적으로 동작했다는 증거는 구분한다.

```text
On-Prem / MSP ── VPN·운영 자동화 ── AWS DR VPC
                                     ├── Public: Web EC2 · Internet Gateway
                                     ├── Private: Doc/EMR EC2 · DB EC2
                                     ├── SSM Interface Endpoint × 2
                                     ├── ECR: Web·EMR 이미지
                                     └── S3: 암호화 DB 백업 저장 위치
```

Terraform 원본 구성은 `ci-cd/infra/terraform/`과 `msp/rootfs/home/cloud/hospital-dr-codex/infra/terraform/`에서 찾을 수 있다. 서버 설정과 복구 절차는 `ci-cd/automation/ansible/`, `msp/`의 운영 코드와 각 runbook을 참조한다. 공개 백업에서는 AWS 리소스와 계정 식별자를 치환했으므로, 이 파일만으로 실제 계정을 재생성하거나 Terraform을 바로 적용할 수는 없다.

<br>

## 새 계정에서 재구성할 때

1. 새 계정·리전·비용 한도를 확정하고, 현재 요구사항에 맞는 VPC·Subnet·보안 그룹·NAT 경로를 설계한다.
2. Terraform 코드와 변수·IAM 권한을 검토하고, 계정별 식별자·주소·도메인 및 SSH/WireGuard 키를 안전한 외부 설정으로 새로 발급한다.
3. 인프라 구축 후 Ansible로 Web·Doc/EMR·DB를 구성하고, ECR 이미지와 S3 백업 정책을 검토한다.
4. Web NAT Instance와 Private 기본 경로의 상태를 읽어 확인한다. 이번 조회에서 Private 기본 경로는 `blackhole`였으므로 실제 통신 성공을 가정하지 않는다.
5. 외부 Secret을 주입한 뒤 서비스 Health, DB 복제·Dump 복구, DNS 전환, Failover/Failback을 각각 검증한다. 기존 계정의 GitHub OIDC 신뢰 관계나 접근 권한을 새 계정에 그대로 복사하지 않는다.

Terraform state·plan, AWS 키, SSM 값, SSH/WireGuard/TLS 개인키, 실제 DB Dump·S3 객체, EC2 디스크·AMI·스냅샷, CloudTrail 원본 로그와 환자 데이터는 이 공개 저장소에 보관하지 않는다. 계정 종료 후에도 필요한 데이터나 증적은 별도의 접근 통제된 저장소에 보관해야 한다.
