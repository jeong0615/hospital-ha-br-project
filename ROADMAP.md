# [ROADMAP] 프로젝트 구축 및 검증 현황

<br>
<br>
<br>
<br>

## 권장 진행 순서와 현재 확인 범위

| 순서 | 단계 | 백업에서 확인되는 범위 | 완료 판단에 필요한 증거 |
| :--- | :--- | :--- | :--- |
| 1 | Physical·GNS3 네트워크 및 방화벽 | 장비 설정·토폴로지 문서 | 장비 상태, 통신·장애 전환 기록 |
| 2 | On-Prem Web·EMR·DB | 서비스 코드·설정·복원 스크립트 | 서비스 Health, 접근 제어, 백업·복원 결과 |
| 3 | MSP 관제 및 ChatOps | Zabbix·Grafana·Wazuh·ChatOps 구성과 systemd 백업 | 운영 연동, 알림·승인·보안 게이트 기록 |
| 4 | AWS DR 및 CI/CD | Terraform·Ansible·Docker·GitHub Actions 정의 | 배포 결과, DR 서비스 Health, 이미지 검증 |
| 5 | Hybrid 연동 및 DB 보호 | VPN·NR·Dump·DNS 전환 절차와 일부 격리 시험 기록 | 실제 On-Prem ↔ AWS 복제·복원·단일 writer 확인 |
| 6 | 승인된 전체 DR 시연과 계측 | 사전 점검·RTO/RPO 증적 수집 코드 | 실제 Failover·Failback 전 구간의 연속 실행 결과와 측정값 |
| 7 | 공개용 백업·문서 인계 | `physical/`, `gns3/`, `web/`, `emr/`, `db/`, `msp/`, `ci-cd/` 백업 | 민감정보 검토와 GitHub 최종 동기화 |

<br>

| 구분 | 담당 영역 | 주요 역할 |
| :--- | :--- | :--- |
| **정승철** | MSP / IaC / Monitoring / AWS DR | 중앙 통합 관제, AWS DR 환경 구축, Slack 2인 승인, 페일오버/페일백, DB 하이브리드 복제 |
| **이재훈** | Web / DNS / Physical / Security / HSRP | Web 서버 구축, 실제 장비 구성 및 활성화, 코어·게이트웨이 이중화, 응용 계층 보안 설정 |
| **윤종민** | EMR / DB / GNS3 / Firewall / CARP | EMR·DB 서버 구축, GNS3 내부망 토폴로지 설계, 방화벽·ESW 스위치 이중화, IDS/IPS 설정 |

<br>
<br>
<br>
<br>

# [On-Premise]
>## [HIS] 병원 인프라 및 내부망
>| 단계 | 항목 | 목표 | 수행 방법 | 기록된 구축 진척 |
>| :--- | :--- | :--- | :--- | :--- |
>| **[H1]** | **병원 서버 구축** | 서비스 인프라 마련 | Ubuntu 22.04 기반 Web, Doc, DB 서버 구축 및 패키지 구성 | **100%** |
>| **[H2]** | **GNS3 네트워크 구성** | 내부 HIS 네트워크 구축 | GNS3 L2/L3 노드 구성 및 내부 네트워크 통신 확인 | **100%** <br> &nbsp; |
>| **[H3]** | **외부 네트워크 연동** | GNS3 외부 통신 경로 확보 | GNS3 Host를 실제 L2 Switch에 연결하고 Router를 통한 외부 통신 확인 | **100%** <br> &nbsp; |
>| **[H4]** | **내부망 세분화** | PC·서버 네트워크 분리 | GNS3에서 VLAN 10(PC), VLAN 30(Doc/DB) 구성 및 통신 검증 | **100%** <br> &nbsp; |
>| **[H5]** | **동적 라우팅** | GNS3 내부 경로 구성 | OSPF 프로세스 구성 및 동적 라우팅 경로 검증 | **100%** <br> &nbsp; |
>| **[H6]** | **GNS3 네트워크 HA** | 내부 Gateway/Core 가용성 확보 | VLAN 10·30 VIP 기반 Gateway 이중화 및 네트워크 장애 전환 검증 | **100%** <br> &nbsp; |
>| **[H7]** | **이중화 방화벽** | 방화벽 가용성 확보 | pfSense VIP 구성 및 CARP 프로토콜 적용 | **100%** <br> &nbsp; |
>| **[H8]** | **경계 보안 및 DMZ 구성** | Web 공개 영역과 내부 업무망 보호 | Web 앞단 DMZ 구성 및 pfSense 방화벽 정책 적용, EMR/DB는 별도 내부망으로 운영 | **100%** <br> &nbsp; |
>| **[H9]** | **네트워크 접근통제** | 불필요한 통신 차단 | ACL 및 Firewall 정책으로 허용된 서비스·포트만 통신 허용 | **100%** <br> &nbsp; |
>| **[H10]** | **도메인 주소** | 외부 Web 서비스 연결 | Cloudflare DNS 및 SSL/TLS 적용 | **100%** <br> &nbsp; |
>| **[H11]** | **웹서버 보안** | Web 서비스 보호 | Cloudflare WAF 및 Application WAF 이중 적용 | **100%** <br> &nbsp; |
>| **[H12]** | **EMR/DB 접근 통제** | 의료 데이터 및 내부 서비스 보호 | EMR/DB의 외부 직접 접근을 차단하고 허용된 서비스 통신만 접근 가능하도록 제한 | **100%** <br> &nbsp; |
>| **[H13]** | **보안 기능 확장** | 침입 탐지·방지 | pfSense IDS/IPS 적용 및 이벤트 탐지 검증 | **100%** <br> &nbsp; |

>## [Physical] 실제 장비
>| 단계 | 항목 | 목표 | 수행 방법 | 기록된 구축 진척 |
>| :--- | :--- | :--- | :--- | :--- |
>| **[P1]** | **L2/L3 장비 기본 설정** | 물리 네트워크 장비 운영 환경 구축 | Hostname, Enable Secret, 관리 IP 및 인터페이스 기본 설정 | **100%** <br> &nbsp; |
>| **[P2]** | **L3 코어 및 VLAN 구성** | Web 및 Uplink 네트워크 구성 | L3-SW1/SW2 코어 구성, VLAN 20(Web) 및 VLAN 100(Uplink) SVI 구성 | **100%** <br> &nbsp; |
>| **[P3]** | **Trunk / Access 구성** | 실제 네트워크 연결 | L2 ↔ L3 Uplink 및 Web Server·GNS3 Host 연결 포트 구성 | **100%** <br> &nbsp; |
>| **[P4]** | **HSRP 구성** | Web Gateway 이중화 | L3-SW1/SW2에서 VLAN 20 HSRP Virtual IP 구성 및 장애 전환 확인 | **100%** <br> &nbsp; |
>| **[P5]** | **L3 Routing 구성** | Web 및 외부 통신 경로 확보 | VLAN 20 ↔ Core/Uplink 구간 L3 라우팅 및 기본 경로 구성 | **100%** <br> &nbsp; |
>| **[P6]** | **RIPv2 구성** | 실제 장비 동적 라우팅 | L3 Core ↔ Router 간 RIPv2 구성 및 경로 정보 교환 확인 | **100%** <br> &nbsp; |
>| **[P7]** | **RSTP 구성** | 실제 스위치 루프 방지 | L2 Switch 및 L3 Switch의 L2 구간 RSTP 설정 | **100%** <br> &nbsp; |
>| **[P8]** | **ACL 적용** | 실제 네트워크 접근 제어 | Web·관리·Uplink 구간의 불필요한 통신 차단 및 허용 서비스 지정 | **100%** <br> &nbsp; |

<br>
<br>
<br>
<br>

# [Cloud]

>## [MSP] 중앙 관제
>| 단계 | 항목 | 목표 | 수행 방법 | 기록된 구축 진척 |
>| :--- | :--- | :--- | :--- | :--- |
>| **[M1]** | **MSP 통합 관제 서버** | 중앙 관제 및 DR 제어 거점 구축 | pfSense 격리 환경에서 Ubuntu 22.04 기반 MSP 서버 및 관리 서비스 구성 | **100%** <br> &nbsp; |
>| **[M2]** | **On-Prem ↔ MSP VPN** | 안전한 관제 통신 경로 확보 | WireGuard 기반 On-Premise ↔ MSP 전용 VPN 구성 및 통신 검증 | **100%** <br> &nbsp; |
>| **[M3]** | **Zabbix 관제 플랫폼** | 인프라 및 DR 상태 수집 | Zabbix Server/Agent/SNMP 기반 서버·네트워크 상태 및 DR 자동화 Item 수집, Problem/Recovery 연계 | **100%** <br> &nbsp; |
>| **[M4]** | **Grafana 통합 대시보드** | 인프라·DR·보안 상태 시각화 | Zabbix API 및 Wazuh OpenSearch Indexer를 Grafana와 연동 | **100%** <br> &nbsp; |
>| **[M5]** | **Wazuh SIEM 플랫폼** | 중앙 보안 로그 분석 환경 구축 | Wazuh Manager, Indexer, Dashboard 구성 및 Grafana Read-Only 연동 | **100%** <br> &nbsp; |
>| **[M6]** | **Terraform / Ansible 관리 환경** | AWS DR 중앙 제어 | MSP에서 Terraform 기반 프로비저닝 및 Ansible 기반 구성·복구 자동화 | **100%** <br> &nbsp; |
>| **[M7]** | **Slack 장애 알림** | 장애 상황 실시간 전달 | 실제 Zabbix Problem/Recovery Event → ChatOps → Slack 자동 알림 연계 | **100%** <br> &nbsp; |
>| **[M8]** | **DR 2인 승인 ChatOps** | 관리자 이중 승인 통제 | Slack Failover·Failback·Emergency 3-App 기반 2인 승인, Global Lock, Audit, Health/Security Gate 및 ngrok HTTPS Endpoint 연동 | **100%** <br> &nbsp; |
>| **[M9]** | **암호화 DB 백업 수집** | 별도 복구 데이터 확보 | On-Prem에서 생성한 `.sql.gz.enc` 및 `.master_status`를 MSP로 전송·관리 | **100%** <br> &nbsp; |
>| **[M10]** | **RTO/RPO 관제 및 증적 수집** | DR 복구 성능 측정 체계 구축 | Zabbix·Grafana 및 Evidence Collector 기반 복구 지표 수집·증적 체계 구성, 전체 E2E RTO/RPO 실측은 NOT_MEASURED로 구분 | **100%** <br> &nbsp; |
>| **[M11]** | **Cloudflare Secret 관리** | DNS 자동화 인증정보 보호 | Cloudflare API Token을 AWS SSM SecureString에서 런타임 조회하도록 통일 | **100%** <br> &nbsp; |
>| **[M12]** | **DR 실행 안정성 / 상태 관리** | 페일오버/페일백 안정성 확보 | Persistent DR State, 실행 Lock, Timeout·Stale 복구, DNS Snapshot/Rollback, Partial Failure 처리 | **100%** <br> &nbsp; |
>| **[M13]** | **네트워크 DR Health 게이트** | 안전한 Failback | SNMP/Zabbix 기반 Network Health를 검증하고 Web/EMR/DB/Network 300초 정상 시 Failback 요청 생성 | **100%** <br> &nbsp; |

>## [CSP] AWS DR
>| 단계 | 항목 | 목표 | 수행 방법 | 기록된 구축 진척 |
>| :--- | :--- | :--- | :--- | :--- |
>| **[A1]** | **AWS VPC 인프라** | DR 클라우드 거점 구축 | Terraform 기반 VPC, Subnet, IGW, Route Table 구성 | **100%** <br> &nbsp; |
>| **[A2]** | **Public / Private Subnet 분리** | DR 서버 보안 영역 분리 | Web은 Public, Doc/EMR·DB는 Private Subnet에 배치 | **100%** <br> &nbsp; |
>| **[A3]** | **Security Group** | 서비스별 네트워크 접근 통제 | Web/Doc/DB별 최소 포트 기반 SG 구성 및 보안 정책 정비 | **100%** <br> &nbsp; |
>| **[A4]** | **Web NAT Instance** | Private 서버 외부 통신 | AWS Web EC2를 NAT Instance로 사용하고 Private Subnet Route를 Web ENI로 구성 | **100%** <br> &nbsp; |
>| **[A5]** | **DR EC2 프로비저닝** | Web/Doc/DB DR 인프라 자동 생성 | Terraform으로 Web, Doc/EMR, DB EC2 생성 | **100%** <br> &nbsp; |
>| **[A6]** | **DR 서버 구성 자동화** | 서버 환경 표준화 | Ansible 기반 Web, Doc/EMR, DB 구성 및 Existing-host 재프로비저닝 E2E | **100%** <br> &nbsp; |
>| **[A7]** | **IAM / SSM 최소권한 구조** | AWS 접근 및 Secret 통제 | IAM 권한 분리, GitHub OIDC, SSM SecureString 및 최소권한 정책 관리 | **100%** <br> &nbsp; |
>| **[A8]** | **DR 서비스 Docker화** | 서비스 실행환경 표준화 | Web/EMR Docker Image 구성, Health Check 및 보안 Hardening | **100%** <br> &nbsp; |
>| **[A9]** | **ECR 이미지 관리** | 검증된 DR 이미지 보관 | Private ECR, Immutable SHA Tag, 보안 Scan 및 검증 이미지 배포 | **100%** <br> &nbsp; |
>| **[A10]** | **GitHub Actions / OIDC** | 안전한 CI/CD 구축 | Build → Trivy → Health → Security Gate → OIDC ECR Push | **100%** <br> &nbsp; |
>| **[A11]** | **AWS S3 원격지 백업** | 오프사이트 DB 백업 확보 | MSP 암호화 Dump를 S3에 업로드하고 Versioning/Lifecycle/SSE 적용 | **100%** <br> &nbsp; |
>| **[A12]** | **DB Dump 복구 자동화** | NR 장애 시 보조 복구 | 암호화 Dump·master_status·File/Position·Checksum 검증 및 복구 Workflow 구성 | **100%** <br> &nbsp; |
>| **[A13]** | **On-Prem → AWS CDC(NR)** | 데이터 손실 최소화 | MySQL Binlog/Position 기반 On-Prem DB → AWS DR DB Native Replication | **100%** <br> &nbsp; |
>| **[A14]** | **DR 생명주기 / FinOps** | DR 운영비 최소화 | EC2 Stop/Start, Web NAT Instance Route, EIP·ENI 유지 및 비용 최적화 | **100%** <br> &nbsp; |
>| **[A15]** | **Cloudflare DNS / TLS DR 전환** | Web 서비스의 안전한 DR 전환 | Origin CA + Full(strict), DNS Snapshot 기반 CNAME ↔ AWS A 전환·복원 및 External E2E 검증 | **100%** <br> &nbsp; |
>| **[A16]** | **AWS Cloud Security / XDR 연계** | AWS DR 보안 이벤트 중앙 관제 | CloudTrail Event History → Collector → Wazuh Indexer → Grafana/XDR 연계 및 실제 이벤트 수집 검증 | **100%** <br> &nbsp; |
>| **[A17]** | **AWS DR 전용 WireGuard** | DB 복제 전용 VPN | `wg-aws-dr` (`10.251.0.0/24`, UDP 51821) 기반 On-Prem DB ↔ AWS DR 복제 경로 구성 | **100%** <br> &nbsp; |

<br>
<br>
<br>
<br>

# [Hybrid]
>| 단계 | 항목 | 테스트 / 검증 내용 | 백업에서 확인한 결과 |
>| :--- | :--- | :--- | :--- |
>| **[T1]** | **네트워크 기본 통신** | GNS3 VLAN 10/30, Physical VLAN 20, 장비 간 통신 및 외부 연결 확인 | **[△]** 설정 문서 확인, 현장 통신 기록 미확인 |
>| **[T2]** | **On-Premise HA 검증** | Gateway HA·OSPF·HSRP·RSTP·RIPv2·CARP 장애 전환 확인 | **[△]** 구성 기록 확인, 전체 장애 전환 기록 미확인 |
>| **[T3]** | **네트워크 보안 검증** | VLAN/ACL, pfSense Firewall, IDS/IPS 허용·차단 확인 | **[△]** 정책·설계 문서 확인, 현장 시험 결과 미확인 |
>| **[T4]** | **Web 보안 검증** | Express WAF, Rate Limit, Cloudflare 설정 및 차단 응답 확인 | **[△]** 코드·설정 확인, 운영 차단 결과 미확인 |
>| **[T5]** | **Hybrid 네트워크 연동** | On-Prem ↔ MSP WireGuard, MSP ↔ AWS 및 DB 복제 경로 확인 | **[△]** 구성·점검 스크립트 확인, 전체 실통신 기록 미확인 |
>| **[T6]** | **DB 백업 / 복구 검증** | On-Prem 암호화 Dump → MSP → S3 → 실제 DR DB 복원·정합성 확인 | **[△]** 격리된 Dump 복원·AWS MySQL 합성 시험 기록 확인; 전체 운영 경로 미확인 |
>| **[T7]** | **DB 실시간 NR 검증** | Position 기반 On-Prem DB → AWS DR DB 복제 및 데이터 적용 확인 | **[△]** NR 코드·절차 확인, 실제 source/replica 연속 증적 미확인 |
>| **[T8]** | **통합 관제 검증** | Grafana 대시보드와 패널 쿼리, 장애·복구 지표의 운영 수집 확인 | **[△]** 대시보드·검사 코드 확인, 운영 패널 실측 결과 미확인 |
>| **[T9]** | **통합 보안 관제 검증** | Wazuh Agent → MSP SIEM 이벤트 탐지 및 Slack 알림 확인 | **[△]** 연동 구성 확인, 현장 이벤트·알림 기록 미확인 |
>| **[T10]** | **ChatOps / DR 승인 검증** | 장애 이벤트 → Slack 2인 승인 → 중복 실행 방지·DR 연계 확인 | **[△]** 승인 코드·시험 확인, 실제 운영 승인 전체 흐름 미확인 |
>| **[T11]** | **Failover / DR 서비스 검증** | 승인 → Terraform/Ansible → AWS DR → DNS 전환 → Web/EMR/DB Health 확인 | **[△]** 구현·모의 시험 확인, 실제 서비스 전환 전체 결과 미확인 |
>| **[T12]** | **Failback 및 데이터 정합성 검증** | AWS DR 데이터의 On-Prem 복귀, DNS 원복과 실제 데이터 정합성 확인 | **[△]** 격리된 MySQL 왕복 시험 확인, 운영 Failback 전체 결과 미확인 |
>| **[T13]** | **End-to-End DR 검증** | On-Prem 장애 → AWS Failover → DR 데이터 생성 → Failback → On-Prem 복귀 및 RTO/RPO 실측 | **[△]** 전체 연속 실행 증적 미확인; RTO/RPO `NOT_MEASURED` |
>| **[T14]** | **운영 자료 백업 및 문서화** | 서버 코드·설정, 네트워크 장비 문서와 CI/CD 파일의 공개용 백업 | **[✓]** `web/`, `emr/`, `db/`, `msp/`, `ci-cd/`, `physical/`, `gns3/` 확인 |
