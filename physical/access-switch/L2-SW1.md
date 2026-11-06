# Cisco Catalyst 2950 L2 Switch

<br>
<br>
<br>
<br>

## 장비 요약

- **Model:** Cisco Catalyst 2950
- **Hostname:** `L2-Switch`
- **Role:** Physical Network Access / Uplink Switch
- **Management VLAN:** `VLAN 1`
- **Management IP:** `IP_REDACTED` (원본 서브넷 마스크 확인)
- **Default Gateway:** `IP_REDACTED`
- **Spanning Tree:** `Rapid-PVST`
- **Switching:** `Access / 802.1Q Trunk`

### 주요 인터페이스

| Interface | Mode | VLAN | 연결 대상 |
| :--- | :--- | :--- | :--- |
| **Fa0/3** | Trunk | 1, 100 | Cisco Router Uplink |
| **Fa0/4** | Trunk | 설정 확인 필요 | 연결 대상 확인 필요 |
| **Fa0/22** | Access | 20 | Web 네트워크 / USB LAN |
| **Fa0/23** | Trunk | 10, 20, 30, 100 | Catalyst 3550 |
| **Fa0/24** | Trunk | 10, 20, 30, 100 | Catalyst 3560 |

### 주요 역할

Cisco Catalyst 2950은 온프레미스 물리 네트워크의 L2 Access Switch로 사용한다.

Router, L3 Core Switch 및 Web 네트워크 연결을 담당하며, Access/Trunk 포트를 통해 VLAN 트래픽을 전달한다.

Rapid-PVST를 적용하여 스위칭 구간의 L2 루프 방지 및 네트워크 안정성을 확보한다.

<br>

## Running Config

```text
version 12.1
no service pad
service timestamps debug uptime
service timestamps log uptime
service password-encryption
hostname L2-Switch
enable secret CREDENTIAL_REDACTED <REDACTED>
username admin password CREDENTIAL_REDACTED <REDACTED>
ip subnet-zero

spanning-tree mode rapid-pvst
no spanning-tree optimize bpdu transmission
spanning-tree extend system-id

interface FastEthernet0/1

interface FastEthernet0/2

interface FastEthernet0/3
 description UPLINK-TO-ROUTER
 switchport trunk allowed vlan 1,100
 switchport mode trunk

interface FastEthernet0/4
 switchport mode trunk

interface FastEthernet0/5

interface FastEthernet0/6

interface FastEthernet0/7

interface FastEthernet0/8

interface FastEthernet0/9

interface FastEthernet0/10

interface FastEthernet0/11

interface FastEthernet0/12

interface FastEthernet0/13

interface FastEthernet0/14

interface FastEthernet0/15

interface FastEthernet0/16

interface FastEthernet0/17

interface FastEthernet0/18

interface FastEthernet0/19

interface FastEthernet0/20

interface FastEthernet0/21

interface FastEthernet0/22
 description WEB-USB-LAN-VLAN20
 switchport access vlan 20
 switchport mode access

interface FastEthernet0/23
 description UPLINK-TO-3550
 switchport trunk allowed vlan 10,20,30,100
 switchport mode trunk

interface FastEthernet0/24
 description UPLINK-TO-3560
 switchport trunk allowed vlan 10,20,30,100
 switchport mode trunk

interface GigabitEthernet0/1

interface GigabitEthernet0/2

interface Vlan1
 ip address IP_REDACTED IP_REDACTED
 no ip route-cache

ip default-gateway IP_REDACTED
ip http server

line con 0

line vty 0 4
 login local
 transport input telnet

line vty 5 15
 login

end
```

<br>

## Interface Status

```text
Interface                  IP-Address      OK? Method Status                Protocol
Vlan1                      IP_REDACTED        YES NVRAM  up                    up
FastEthernet0/1            unassigned      YES unset  down                  down
FastEthernet0/2            unassigned      YES unset  up                    up
FastEthernet0/3            unassigned      YES unset  up                    up
FastEthernet0/4            unassigned      YES unset  up                    up
FastEthernet0/5            unassigned      YES unset  up                    up
FastEthernet0/6            unassigned      YES unset  down                  down
FastEthernet0/7            unassigned      YES unset  down                  down
FastEthernet0/8            unassigned      YES unset  down                  down
FastEthernet0/9            unassigned      YES unset  down                  down
FastEthernet0/10           unassigned      YES unset  down                  down
FastEthernet0/11           unassigned      YES unset  down                  down
FastEthernet0/12           unassigned      YES unset  down                  down
FastEthernet0/13           unassigned      YES unset  down                  down
FastEthernet0/14           unassigned      YES unset  down                  down
FastEthernet0/15           unassigned      YES unset  down                  down
FastEthernet0/16           unassigned      YES unset  down                  down
FastEthernet0/17           unassigned      YES unset  down                  down
FastEthernet0/18           unassigned      YES unset  down                  down
FastEthernet0/19           unassigned      YES unset  down                  down
FastEthernet0/20           unassigned      YES unset  down                  down
FastEthernet0/21           unassigned      YES unset  down                  down
FastEthernet0/22           unassigned      YES unset  up                    up
FastEthernet0/23           unassigned      YES unset  up                    up
FastEthernet0/24           unassigned      YES unset  up                    up
GigabitEthernet0/1         unassigned      YES unset  down                  down
GigabitEthernet0/2         unassigned      YES unset  down                  down
```
