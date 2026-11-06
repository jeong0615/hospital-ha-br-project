# AWS DR 읽기 전용 구성 기록

관측 시점: 2026-11-06 (Asia/Seoul)

조회 범위: `ap-northeast-2`의 프로젝트 관련 EC2·VPC와 계정의 ECR·S3·Lambda 구성

방법: AWS CLI `describe-*` / `list-*` 및 S3 버킷 설정 조회. 리소스 식별자, 주소, 이름, 정책 본문, Secret 값과 데이터 내용은 기록하지 않는다.

<br>

| 항목 | 관측 결과 |
| :--- | :--- |
| 프로젝트 관련 EC2 | 3대: Web 1, Doc/EMR 1, DB 1; 조회 시 모두 `stopped` |
| EC2가 속한 VPC | 1개 |
| 해당 VPC의 Subnet | 4개; 인스턴스 시작 시 공인 IP 자동 할당 설정 1개 |
| 해당 VPC의 Route Table | 3개; 기본 경로 대상 유형으로 Internet Gateway·Instance·Network Interface 확인 |
| 해당 VPC의 Security Group | 5개 |
| 해당 VPC의 Internet Gateway | 1개 |
| 해당 VPC의 NAT Gateway | 0개 |
| 해당 VPC의 VPC Endpoint | 2개 |
| ECR Repository | 2개(Web·EMR); 둘 다 immutable tag, scan-on-push 설정 |
| S3 Bucket | 프로젝트 명칭과 일치하는 1개; Versioning, 암호화 설정, Public Access Block, Lifecycle 규칙 확인 |
| Lambda Function | 리전에서 1개 확인; 이 기록만으로 함수의 프로젝트 소속·실행 상태는 판정하지 않음 |

이 표는 조회 시점의 구성 메타데이터다. 서버가 중지돼 있었으므로 서비스 Health, NAT 실제 통신, DB 복제, S3 객체의 복원 가능성, IAM 권한의 유효성, 전체 DR 시나리오 성공은 확인하지 않았다. 프로젝트 외 리소스의 존재 여부나 다른 리전의 상태도 이 기록으로 판단하지 않는다.
