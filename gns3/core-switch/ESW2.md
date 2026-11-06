# ESW2

<br>
<br>
<br>
<br>

## 장비 요약

- **Hostname:** `ESW2`
- **Role:** GNS3 내부 네트워크 및 pfSense 방화벽 HA 연계
- **VLAN 10:** `IP_REDACTED/24 (VIP: IP_REDACTED)`
- **VLAN 30:** `IP_REDACTED/24 (VIP: IP_REDACTED)`
- **VLAN 100:** `IP_REDACTED/24 (VIP: IP_REDACTED)`
- **HSRP Priority:** `100 (All Groups)`
- **HSRP State:** `Standby (검증 시점 기준)`
- **HSRP Preempt:** `Enabled`
- **IP Routing:** `Enabled`
- **Static Default Route:** `IP_REDACTED`
- **DHCP:** `VLAN 10 (LAN10 Pool)`
- **Switching:** `Access / Trunk (802.1Q)`
- **Spanning Tree:** `Enabled (PortFast on Access Ports)`
- **Firewall HA:** `pfSense CARP 연계`

<br>

## 주요 역할

ESW2는 GNS3 내부 네트워크에서 VLAN 10/30/100의
통신 경로를 제공하며, ESW1과 함께 게이트웨이
이중화 환경을 구성한다.

HSRP 우선순위 100을 설정하여 ESW1보다 낮은
우선순위를 갖도록 구성하였으며, pfSense CARP
방화벽 이중화 환경의 네트워크 연결도 지원한다.

방화벽의 MASTER/BACKUP 전환은 pfSense CARP에서
별도로 수행한다.

<br>

## Running Config

```text
conf t
hostname ESW2
ip routing
vlan 10
vlan 30
vlan 100
exit
ip dhcp excluded-address IP_REDACTED IP_REDACTED
ip dhcp pool LAN10
network IP_REDACTED IP_REDACTED
default-router IP_REDACTED
dns-server IP_REDACTED
exit
interface vlan 10
ip address IP_REDACTED IP_REDACTED
standby 10 ip IP_REDACTED
standby 10 priority 100
standby 10 preempt
exit
interface vlan 30
ip address IP_REDACTED IP_REDACTED
standby 30 ip IP_REDACTED
standby 30 priority 100
standby 30 preempt
exit
interface vlan 100
ip address IP_REDACTED IP_REDACTED
standby 100 ip IP_REDACTED
standby 100 priority 100
standby 100 preempt
exit
interface range f1/0
switchport mode access
switchport access vlan 100
spanning-tree portfast
exit
interface range f1/14
switchport mode access
switchport access vlan 100
spanning-tree portfast
exit
interface f1/1
switchport mode access
switchport access vlan 10
spanning-tree portfast
exit
interface f1/2
switchport mode access
switchport access vlan 30
spanning-tree portfast
exit
interface f1/15
switchport trunk encapsulation dot1q
switchport mode trunk
exit
ip route IP_REDACTED IP_REDACTED IP_REDACTED
end
wr
```
