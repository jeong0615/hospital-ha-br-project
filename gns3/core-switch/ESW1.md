# ESW1

<br>
<br>
<br>
<br>

## 장비 요약

- **Hostname:** `ESW1`
- **Role:** GNS3 내부 네트워크 및 pfSense 방화벽 이중화 연계
- **VLAN 10:** `IP_REDACTED/24`
- **VLAN 30:** `IP_REDACTED/24`
- **VLAN 100:** `IP_REDACTED/24`
- **Switching:** Access / Trunk 구성
- **IP Routing:** Enabled
- **Spanning Tree:** Enabled (PortFast on Access Ports)
- **Firewall HA:** pfSense CARP 연계

<br>

## 주요 역할

ESW1은 GNS3 내부 네트워크에서 VLAN별 통신 경로를 제공하고, pfSense 방화벽 이중화 환경의 네트워크 연결을 지원한다.

방화벽의 Active/Backup 전환 및 공유 VIP 관리는 pfSense CARP에서 수행한다.

<br>

## Running Config

```text
conf t
hostname ESW1
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
standby 10 priority 110
standby 10 preempt
exit
interface vlan 30
ip address IP_REDACTED IP_REDACTED
standby 30 ip IP_REDACTED
standby 30 priority 110
standby 30 preempt
exit
interface vlan 100
ip address IP_REDACTED IP_REDACTED
standby 100 ip IP_REDACTED
standby 100 priority 110
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
