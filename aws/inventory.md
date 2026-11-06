# AWS DR 읽기 전용 구성 기록

관측 시점: 2026-11-06 (Asia/Seoul)

조회 범위: `ap-northeast-2`의 프로젝트 관련 EC2·VPC와 계정의 ECR·S3·Lambda 구성

방법: AWS CLI `describe-*` / `list-*` 및 S3 버킷 설정 조회. 리소스 식별자, 주소, 이름, 정책 본문, Secret 값과 데이터 내용은 기록하지 않는다.

<br>

| 항목 | 관측 결과 |
| :--- | :--- |
| 프로젝트 관련 EC2 | 3대: Web 1, Doc/EMR 1, DB 1; 조회 시 모두 `stopped` |
| EC2가 속한 VPC | 1개 |
| 해당 VPC의 Subnet | 4개, Availability Zone 2개; 공인 IP 자동 할당 설정 1개 |
| 해당 VPC의 Route Table | 3개; Public 기본 경로는 Internet Gateway로 `active`, Private 기본 경로의 Web Instance/Network Interface 대상은 조회 시 `blackhole` |
| 해당 VPC의 Security Group | 5개 |
| 해당 VPC의 Internet Gateway | 1개 |
| 해당 VPC의 NAT Gateway | 0개 |
| 해당 VPC의 VPC Endpoint | 2개: SSM·SSM Messages Interface Endpoint, 모두 `available`; S3 Gateway Endpoint는 확인되지 않음 |
| 프로젝트 EC2에 연결된 EBS Volume | 3개; 디스크 내용·스냅샷은 조회하거나 백업하지 않음 |
| ECR Repository | 2개(Web·EMR), 각각 이미지 메타데이터 10건; 둘 다 immutable tag, scan-on-push 설정. 이미지 본문은 미보관 |
| S3 Bucket | 프로젝트 명칭과 일치하는 1개; Versioning, 암호화 설정, Public Access Block, Lifecycle 규칙 확인 |
| Lambda Function | 리전에서 1개 확인, 조회 시 `Active`; 이 기록만으로 프로젝트 소속이나 정상 실행을 판정하지 않음 |
| EventBridge Rule | 리전에서 2개 확인: `ENABLED` 1개, `DISABLED` 1개. 규칙 내용과 실행 이력은 미보관 |
| CloudWatch Log Group | 리전에서 1개 확인. 로그 내용은 미보관 |
| IAM OIDC Provider | 계정에서 1개 확인. 신뢰 정책·계정 식별자는 미보관 |
| CloudTrail Trail | 리전 조회에서 0개. Event History 사용 여부·과거 이벤트를 뜻하지 않음 |

이 표는 조회 시점의 구성 메타데이터다. Private 기본 경로의 `blackhole` 상태는 그 시각의 AWS 응답이며, 재가동 후 자동 복구 여부는 검증하지 않았다. 서버가 중지돼 있었으므로 서비스 Health, NAT 실제 통신, DB 복제, S3 객체의 복원 가능성, IAM 권한의 유효성, 전체 DR 시나리오 성공은 확인하지 않았다. 프로젝트 외 리소스의 존재 여부나 다른 리전의 상태도 이 기록으로 판단하지 않는다.
