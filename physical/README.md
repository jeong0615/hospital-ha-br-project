# Physical Network

이 디렉터리는 Hospital DR 프로젝트에서 사용한 **실제 Cisco 네트워크 장비의 구성 정보, 설정 및 검증 결과**를 관리한다.

Router, L2 Access Switch, L3 Core Switch의 설정을 기록하여 물리 네트워크의 구조와 동작 방식을 확인하고, 향후 네트워크 재구성 시 참고할 수 있도록 문서화하였다.

본 디렉터리의 파일은 실제 장비에 설정을 자동으로 적용하거나 네트워크 환경을 변경하지 않는다.

<br>

## 1. 디렉터리 구조

```text
physical/
├── router/
│   └── R1.md
├── access-switch/
│   └── L2-SW1.md
├── core-switch/
│   ├── Active-L3-SW.md
│   └── Standby-L3-SW.md
└── README.md
```

각 장비별 문서에는 주요 설정, 실제 Running Configuration 및 인터페이스 상태를 기록한다.

<br>

## 2. 물리 네트워크 구성

Physical Network는 실제 Cisco Router 및 L2/L3 Switch로 구성되며, 외부 네트워크 연결과 Web 서비스 네트워크의 게이트웨이 이중화를 담당한다.

| 구분 | 장비 모델 | 주요 역할 |
|---|---|---|
| **Router** | Cisco 2800 Series | 외부망 연결, RIPv2, NAT/PAT |
| **L2 Access Switch** | Catalyst 2950 | VLAN 및 Access/Trunk 연결 |
| **Active L3 Core** | Catalyst 3560 | HSRP Active Gateway, Routing, ACL |
| **Standby L3 Core** | Catalyst 3550 | HSRP Standby Gateway, Routing, ACL |

### 주요 VLAN

| VLAN | 역할 | 구성 |
|---|---|---|
| **VLAN 1** | 장비 관리 및 기본 네트워크 | L2 관리 SVI 등 |
| **VLAN 20** | Web 서비스 네트워크 | L3 HSRP Gateway |
| **VLAN 100** | Router / Core Uplink | 802.1Q 및 L3 Routing |

L2 스위치의 일부 Trunk 설정에는 VLAN 10/30도 허용되어 있으나, 제공된 Physical L3 설정에서 확인되는 SVI는 VLAN 20/100이다.

GNS3 내부 사용자망(VLAN 10)과 EMR/DB망(VLAN 30)은 별도의 GNS3 네트워크 문서에서 관리한다.

<br>

## 3. 장비 연결 관계

| 출발 장비 | Interface | 대상 장비 | Interface |
|---|---|---|---|
| **Cisco 2800** | Fa0/1 | Catalyst 2950 | Fa0/3 |
| **Catalyst 2950** | Fa0/23 | Catalyst 3550 (Standby) | Fa0/21 |
| **Catalyst 2950** | Fa0/24 | Catalyst 3560 (Active) | Fa0/22 |
| **Catalyst 2950** | Fa0/22 | Web 네트워크 | VLAN 20 Access |

Router의 `Fa0/1.100` 서브인터페이스는 VLAN 100의 802.1Q 트래픽을 처리하도록 구성하였다.

세부 IP 주소 및 추가 연결 관계는 장비별 설정 문서와 별도 원본 자료를 기준으로 확인한다.

<br>

## 4. 주요 네트워크 기술

| 기술 | 적용 장비 | 역할 |
|---|---|---|
| **RIPv2** | Router / L3 Switch | 동적 라우팅 및 경로 정보 교환 |
| **Static Route** | Router / L3 Switch | 목적지별 경로 지정 |
| **HSRP** | Catalyst 3560 / 3550 | VLAN 20 Gateway 이중화 |
| **Rapid-PVST** | L2 / L3 Switch | L2 루프 방지 |
| **802.1Q** | Router / L2 / L3 Switch | VLAN 태깅 및 Trunk 연결 |
| **NAT/PAT** | Cisco 2800 | 내부 주소의 외부 통신 지원 |
| **Extended ACL** | Catalyst 3560 / 3550 | Web 네트워크 접근 제어 |
| **LACP** | Catalyst 3560 / 3550 | EtherChannel 구성 기록 |

LACP 기반 Port-channel12는 두 L3 스위치에 설정되어 있으나, 기록된 Interface Status에서는 Down 상태이다. 따라서 실제 활성 이중화 경로로 분류하지 않는다.

<br>

## 5. HSRP 게이트웨이 이중화

Physical Network에서는 Catalyst 3560과 Catalyst 3550을 사용하여 VLAN 20의 HSRP Gateway 이중화를 구성하였다.

| 항목 | Active | Standby |
|---|---|---|
| **장비** | Catalyst 3560 | Catalyst 3550 |
| **Hostname** | `L3-SW2` | `Standby-L3-SW` |
| **HSRP Group** | 20 | 20 |
| **Priority** | 250 | 200 |
| **State** | Active | Standby |
| **Virtual IP** | `IP_REDACTED` | `IP_REDACTED` |
| **Preempt** | Enabled | Enabled |

각 장비의 HSRP 상태는 기록 당시의 확인 결과를 기준으로 한다.

HSRP 우선순위와 상태를 통해 Active/Standby 역할을 구분하며, 실제 장애 전환 성공 여부는 별도의 테스트 결과로 검증한다.

<br>

## 6. 네트워크 보안

Physical Network에서는 Router의 NAT/PAT와 L3 Switch의 ACL을 통해 네트워크 트래픽을 제어한다.

| 구분 | 적용 위치 | 역할 |
|---|---|---|
| **NAT/PAT** | Cisco 2800 | 지정된 내부 네트워크의 주소 변환 |
| **WEB-FILTER ACL** | L3 Switch VLAN 20 | Web 네트워크의 Inbound 접근 제어 |
| **VLAN 분리** | L2/L3 Switch | Web 및 Uplink 네트워크 구분 |
| **Management** | 장비 관리 인터페이스 | 관리 IP 및 접근 설정 관리 |

`WEB-FILTER`는 VLAN 20 SVI에 Inbound 방향으로 적용되어 있으며, 허용 및 차단 정책은 장비별 Running Configuration에서 확인할 수 있다.

<br>

## 7. 장비별 설정 기록

각 장비 문서는 다음 순서로 작성한다.

1. 장비 요약
2. Running Config
3. Interface Status
4. 프로토콜 상태 및 검증 결과 (해당 장비)

설정 및 상태 확인에 사용하는 주요 명령어는 다음과 같다.

```cisco
show running-config
show ip interface brief
show ip route
show ip protocols
show vlan brief
show interfaces trunk
show standby brief
show spanning-tree
show etherchannel summary
show ip nat statistics
```

모든 명령어를 모든 장비에서 실행하는 것은 아니며, 실제 모델과 지원 기능에 맞는 명령어만 사용한다.

<br>

## 8. 네트워크 검증 항목

| 구분 | 검증 항목 |
|---|---|
| **Interface** | 주요 장비 및 Uplink의 Up/Up 상태 |
| **VLAN** | VLAN 생성 및 Access/Trunk 동작 |
| **Routing** | RIPv2 및 Static Route 상태 |
| **HSRP** | Active/Standby 역할 및 Gateway VIP |
| **Spanning Tree** | Rapid-PVST 상태 |
| **EtherChannel** | LACP 구성 및 실제 링크 상태 |
| **NAT/PAT** | 주소 변환 설정과 실제 통신 |
| **ACL** | 허용·차단 정책 동작 |
| **Connectivity** | Router ↔ L2 ↔ L3 통신 |
| **Service** | Web 및 GNS3 연계 통신 |
| **Recovery** | 장애 전환 및 복구 후 통신 |

검증 결과는 실제 명령어 출력과 함께 기록하며, 설정 존재 여부와 실제 기능 동작 여부를 구분한다.

검증하지 않은 항목은 임의로 PASS 처리하지 않는다.

<br>

## 9. 네트워크 재구성 순서

새로운 Physical Network 환경에서 장비를 재구성할 경우 다음 순서를 기준으로 한다.

1. Cisco 장비 모델 및 Interface 확인
2. 실제 장비 간 케이블 연결 확인
3. Hostname 및 기본 관리 정보 구성
4. VLAN 및 Access/Trunk 구성
5. Router Interface 및 802.1Q Subinterface 구성
6. L3 Core Switch의 SVI 및 Routing 구성
7. RIPv2 및 필요한 Static Route 구성
8. VLAN 20의 HSRP 구성
9. NAT/PAT 및 ACL 정책 구성
10. Spanning Tree 및 EtherChannel 설정 확인
11. 장비별 Interface 및 Routing 상태 확인
12. 네트워크 통신과 장애 전환 검증

설정 적용 전에는 장비의 기존 Running Configuration과 네트워크 상태를 확인한다.

공개 문서의 IP 주소와 인증 정보는 비식별화되어 있으므로, 실제 재구성 시에는 별도로 보관한 원본 자료가 필요하다.

<br>

## 10. Git 제외 및 별도 관리 항목

다음 정보는 Repository에 직접 저장하지 않는다.

- 실제 비밀번호 및 인증 정보
- Enable Secret 및 암호 해시
- Private Key 및 인증서 비밀키
- SNMP Community String
- VPN 관련 Secret
- 민감정보가 포함된 장비 로그
- 라이선스가 필요한 IOS/Image 파일

설정 문서의 민감정보는 다음과 같은 Placeholder로 비식별화한다.

```text
IP_REDACTED
CREDENTIAL_REDACTED
REDACTED_SECRET
```

<br>

## 11. 안전 원칙

- 실제 네트워크 장비의 설정을 문서화 목적으로 임의 변경하지 않는다.
- 장비 초기화 및 재부팅 명령어를 백업 과정에서 실행하지 않는다.
- 실제 Running Configuration 및 검증 결과를 기준으로 기록한다.
- Physical Network와 GNS3 Network의 설정을 구분한다.
- 활성 구성과 비활성 구성을 명확히 구분한다.
- GitHub 업로드 전 인증 정보 및 민감정보를 검토한다.
