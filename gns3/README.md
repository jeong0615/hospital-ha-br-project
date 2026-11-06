# GNS3 Network

이 디렉터리는 Hospital DR 프로젝트의 **GNS3 기반 병원 내부 네트워크**를 문서화한 공간이다.

네트워크 토폴로지, 장비별 설정, IP/VLAN 구성 및 검증 결과를 관리하며, GNS3 프로젝트 원본에만 의존하지 않고 주요 네트워크의 설계와 설정 내용을 확인할 수 있도록 구성하였다.

본 디렉터리의 문서와 설정 기록은 실제 네트워크 구성을 자동으로 변경하거나 실행하지 않는다.

<br>

## 1. 디렉터리 구조

```text
gns3/
├── core-switch/
│   ├── ESW1.md
│   └── ESW2.md
└── README.md
```

<br>

## 2. 네트워크 구성

GNS3 환경은 병원 내부 네트워크를 구성하며, **pfSense Firewall, ESW Core Switch, Access Switch 및 서버·클라이언트**로 구성된다.

실제 Cisco 네트워크와 연동하여 내부 사용자망 및 EMR/DB 서비스망의 통신과 접근 통제를 수행한다.

| 구분 | 네트워크 / IP | 역할 |
|---|---|---|
| Firewall WAN | `IP_REDACTED/24` | 외부 네트워크 연결 |
| WAN CARP VIP | `IP_REDACTED` | pfSense WAN 이중화 |
| Firewall LAN | `IP_REDACTED/24` | Firewall ↔ 내부 네트워크 연결 |
| LAN CARP VIP | `IP_REDACTED` | pfSense LAN 이중화 |
| VLAN 10 | `IP_REDACTED/24` | 내부 사용자 PC |
| VLAN 30 | `IP_REDACTED/24` | EMR / DB 서버 |
| VLAN 100 | `IP_REDACTED/24` | ESW 연결 및 Uplink 구간 |
| DB | `IP_REDACTED` | MySQL Database |
| EMR | `IP_REDACTED` | EMR Service |

실제 Interface IP, VLAN Gateway 및 Port 연결 관계는 장비별 설정 문서에서 관리한다.

### 네트워크 분리 정책

- **VLAN 10:** 병원 내부 사용자 PC 네트워크
- **VLAN 30:** EMR 및 DB 서비스 네트워크
- **VLAN 100:** ESW 스위치의 연결 및 Uplink 구간
- **Firewall:** pfSense 정책에 따라 네트워크 간 접근 통제
- **EMR/DB:** 외부 인터넷 직접 접근 제한

Web 서비스는 별도의 Physical VLAN 20 환경에서 운영하며, Web 앞단에 DMZ를 구성하였다. EMR/DB 영역에는 별도의 DMZ를 구성하지 않는다.

<br>

## 3. 주요 장비

### 3.1 Firewall — pfSense

- **pf1:** pfSense Firewall
- **pf2:** pfSense Firewall

pfSense는 병원 내부 네트워크의 접근 제어와 방화벽 이중화를 담당한다.

주요 기록 항목은 다음과 같다.

- WAN / LAN Interface
- WAN / LAN CARP VIP
- Firewall Rule
- NAT
- Default Route
- CARP MASTER / BACKUP 상태
- Failover 및 Failback 검증 결과

**방화벽 이중화는 pfSense CARP를 사용하며, ESW 스위치의 HSRP와 구분한다.**

### 3.2 Core Switch — ESW1 / ESW2

- **ESW1:** GNS3 Core Switch, HSRP Priority 110
- **ESW2:** GNS3 Core Switch, HSRP Priority 100

ESW1과 ESW2는 GNS3 내부 VLAN 구성과 네트워크 연결을 담당하며, pfSense 방화벽 이중화 환경과 연계된다.

두 장비의 설정 기록에는 VLAN 10/30/100의 HSRP 설정이 포함되어 있다.

주요 기록 항목은 다음과 같다.

- Interface 구성
- VLAN 10 / 30 / 100
- SVI 및 IP Address
- HSRP Virtual IP
- HSRP Priority / Preempt
- Access / Trunk
- Static Route
- DHCP
- Spanning Tree
- Core Switch 간 연결
- 실제 HSRP 상태 및 통신 검증 결과

HSRP의 실제 Active/Standby 상태는 검증 시점의 장비 상태를 기준으로 기록한다.

### 3.3 Access Switch

- Switch1
- Switch2
- Switch3

Access Switch는 PC 및 서버의 물리·논리 연결과 VLAN 전달을 담당한다.

주요 기록 항목은 다음과 같다.

- Access Port
- Trunk Port
- VLAN 할당
- 연결 Host
- Uplink 연결
- VLAN / Trunk 확인 결과

별도 설정이 거의 없는 L2 Switch는 연결 관계와 핵심 설정만 문서화한다.

### 3.4 Host

주요 Host의 네트워크 설정을 기록한다.

- IP Address
- Subnet Mask
- Default Gateway
- VLAN
- 연결 Switch / Port
- DHCP 또는 Static 여부
- Ping 및 서비스 통신 결과

<br>

## 4. 이중화 및 라우팅 구성

| 구분 | 기술 | 역할 |
|---|---|---|
| Core Gateway | HSRP | ESW1·ESW2의 Gateway VIP 설정 |
| Firewall HA | CARP | pfSense 방화벽 VIP 공유 및 장애 전환 |
| Routing | Static Route | 목적지 네트워크 및 기본 경로 설정 |
| Dynamic Routing | OSPF | 실제 적용된 GNS3 라우팅 구간의 경로 교환 |
| Switching | Spanning Tree | L2 루프 방지 및 스위칭 안정성 확보 |

HSRP와 CARP는 각각 스위치 게이트웨이와 방화벽 이중화를 담당한다.

ESW 장비에서 설정한 HSRP 우선순위와 실제 운영 상태는 구분하여 기록하며, OSPF는 해당 프로토콜이 실제 적용된 장비의 설정 및 Neighbor 결과를 기준으로 검증한다.

<br>

## 5. 장비별 설정 기록

네트워크 재구성에 필요한 핵심 설정 명령어와 검증 결과를 장비별로 기록한다.

```text
Interface
IP Address
VLAN
Access / Trunk
SVI
HSRP
Routing
OSPF (적용 장비)
Spanning Tree
Gateway
Firewall Rule
NAT
CARP
```

Cisco 계열 장비의 주요 확인 명령어는 다음과 같다.

```cisco
show ip interface brief
show vlan brief
show interfaces trunk
show standby brief
show ip route
show ip ospf neighbor
show spanning-tree
show running-config
```

각 명령어는 실제 장비에서 지원하고 설정된 기능에 맞춰 사용한다.

설정 명령어 목록은 `Configuration Commands`로, 장비에서 출력한 실제 설정은 `Running Configuration`으로 구분하여 기록한다.

<br>

## 6. 네트워크 검증 항목

GNS3 네트워크 구성 후 다음 항목을 확인한다.

| 구분 | 검증 항목 |
|---|---|
| VLAN | VLAN 10 / 30 내부 통신 |
| VLAN 간 통신 | 허용된 서비스 통신 및 차단 정책 |
| Host | PC → EMR 서비스 접근 |
| Database | 허용된 Host의 DB 접근 |
| Gateway | DB / EMR → Gateway 통신 |
| Routing | Static Route 및 실제 적용된 OSPF 경로 |
| Switching | Trunk, VLAN 전달 및 Spanning Tree 상태 |
| HSRP | ESW1 / ESW2 상태 및 Gateway 통신 |
| Firewall | pfSense Rule 및 NAT 동작 |
| CARP | MASTER/BACKUP 상태 및 방화벽 장애 전환 |
| External | 네트워크별 외부 통신 허용·차단 결과 |
| Recovery | 장애 복구 후 정상 통신 |

### 검증 결과 기록

단순히 `성공`이라고 기록하지 않고, 가능한 경우 실제 명령어와 실행 결과를 함께 보관한다.

검증 명령어 예시:

```cisco
show standby brief
show ip route
show ip ospf neighbor
```

검증 기록에는 다음 내용을 포함한다.

- 테스트 대상 장비
- 검증 명령어
- 실행 시점
- 예상 결과
- 실제 결과
- PASS / FAIL

방화벽 정책에 의해 의도적으로 차단된 통신은 차단 결과가 정책과 일치하는지 확인하여 판정한다.

<br>

## 7. GNS3 재구성 순서

새로운 GNS3 환경에서 네트워크를 재구성할 경우 다음 순서를 기준으로 한다.

1. GNS3 Appliance 및 필요한 Image를 준비한다.
2. 토폴로지 자료에 따라 네트워크 장비를 배치한다.
3. 장비 간 Interface 연결을 구성한다.
4. Access Switch의 VLAN 및 Port를 설정한다.
5. ESW Core Switch의 VLAN, Trunk, SVI를 구성한다.
6. ESW1 / ESW2의 HSRP 설정을 적용한다.
7. Static Routing 및 실제 필요한 OSPF 설정을 적용한다.
8. pfSense WAN/LAN Interface를 구성한다.
9. pfSense Firewall Rule, NAT 및 CARP를 구성한다.
10. PC, EMR, DB의 IP 및 Gateway를 설정한다.
11. VLAN별 통신과 서비스 접근 정책을 확인한다.
12. Routing, HSRP 및 CARP 상태를 확인한다.
13. 장애 전환과 복구 후 통신을 검증한다.

재구성 과정에서 필요한 실제 IP 주소 및 장비 연결 정보는 별도 보관한 원본 자료를 기준으로 적용한다.

<br>

## 8. GNS3 Project 파일

GNS3 Project 원본을 별도로 보관하는 경우 사용한 GNS3 버전과 Appliance/Image 정보를 함께 기록한다.

Cisco IOS 등 라이선스가 필요한 Image는 GitHub Repository에 직접 포함하지 않는다.

새로운 환경에서 재구성할 경우 필요한 Image를 적법하게 별도로 준비하고, 토폴로지와 장비별 설정 문서를 기준으로 복원한다.

<br>

## 9. Git 제외 및 별도 관리 항목

다음 항목은 Repository에 직접 저장하지 않는다.

- Cisco IOS 등 라이선스가 필요한 Image
- VM Disk Image
- 대용량 GNS3 Runtime 파일
- 실제 비밀번호 및 Credential
- Private Key 및 Secret
- 인증 토큰
- 민감정보가 포함된 Packet Capture

공개 설정 문서의 민감정보는 비식별화한다.

```text
IP_REDACTED
REDACTED_SECRET
REDACTED_PASSWORD
```

비식별화된 설정은 그대로 실행 가능한 완전한 복구 설정이 아니며, 실제 네트워크 재구성에는 별도로 관리하는 원본 주소 및 설정 정보가 필요하다.

<br>

## 10. 안전 원칙

- GNS3 설정과 실제 Physical Network 설정을 구분한다.
- 기존 GNS3 Project를 문서화하기 위해 장비 설정을 임의로 초기화하지 않는다.
- 실제 장비 설정과 명령어를 기준으로 문서화한다.
- HSRP와 CARP의 역할 및 적용 장비를 구분한다.
- 실제 검증하지 않은 결과를 PASS로 기록하지 않는다.
- 라이선스가 필요한 IOS/Image 파일을 업로드하지 않는다.
- GitHub 업로드 전 Credential 및 민감정보를 점검한다.
