#!/usr/bin/env bash
set -euo pipefail

# Read-only controller/local status check. It never calls Terraform apply,
# destroy, route mutation, instance mutation, or firewall mutation.

EXPECTED_REGION="${AWS_REGION:-ap-northeast-2}"
EXPECTED_VPC_ID="AWS_RESOURCE_ID_REDACTED"
EXPECTED_WEB_INSTANCE_ID="AWS_RESOURCE_ID_REDACTED"
EXPECTED_WEB_PRIVATE_IP="10.20.10.10"
EXPECTED_WEB_PUBLIC_IP="52.79.244.146"
EXPECTED_WEB_ENI="AWS_RESOURCE_ID_REDACTED"
EXPECTED_PRIVATE_ROUTE_TABLE="AWS_RESOURCE_ID_REDACTED"
EXPECTED_WEB_NAT_INTERFACE="wg-aws-dr"
EXPECTED_NAT_GATEWAY_ID="AWS_RESOURCE_ID_REDACTED"
EXPECTED_NAT_EIP_ALLOCATION_ID="AWS_RESOURCE_ID_REDACTED"
EXPECTED_NAT_EIP_PUBLIC_IP="15.165.140.84"
FORWARD_CHAIN="HOSPITAL-DR-NAT-FWD"
POSTROUTING_CHAIN="HOSPITAL-DR-NAT-POSTROUTING"
LOCAL_CHECK=0

if [ "${1:-}" = "--local" ]; then
  LOCAL_CHECK=1
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "Usage: $0 [--local]" >&2
  exit 2
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "NAT_INSTANCE_STATUS=BLOCKED"
  echo "BLOCKER=AWS_CLI_UNAVAILABLE"
  exit 2
fi

aws_query() {
  aws ec2 "$@" --region "$EXPECTED_REGION" --output text 2>/dev/null
}

instance_record="$(aws_query describe-instances --instance-ids "$EXPECTED_WEB_INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].[State.Name,PrivateIpAddress,PublicIpAddress,SourceDestCheck,SubnetId,NetworkInterfaces[0].NetworkInterfaceId]' || true)"
route_record="$(aws_query describe-route-tables --route-table-ids "$EXPECTED_PRIVATE_ROUTE_TABLE" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].[State,NatGatewayId,NetworkInterfaceId] | [0]" || true)"
eip_record="$(aws_query describe-addresses --allocation-ids "$EXPECTED_NAT_EIP_ALLOCATION_ID" \
  --query 'Addresses[0].[AllocationId,PublicIp,AssociationId]' || true)"
nat_records="$(aws_query describe-nat-gateways --filter "Name=vpc-id,Values=$EXPECTED_VPC_ID" \
  --query 'NatGateways[].[NatGatewayId,State,SubnetId,NatGatewayAddresses[0].AllocationId,NatGatewayAddresses[0].PublicIp]' || true)"

if [ -z "$instance_record" ]; then
  echo "NAT_INSTANCE_STATUS=BLOCKED"
  echo "BLOCKER=AWS_READ_ONLY_QUERY_FAILED"
  exit 2
fi

read -r web_state web_private web_public source_dest web_subnet web_eni <<< "$instance_record"
read -r route_state route_nat route_eni <<< "${route_record:-None}"
read -r eip_alloc eip_public eip_assoc <<< "${eip_record:-None}"

echo "WEB_INSTANCE_STATE=${web_state:-UNKNOWN}"
echo "WEB_PRIVATE_IP=${web_private:-UNKNOWN}"
echo "WEB_PUBLIC_IP=${web_public:-UNKNOWN}"
echo "WEB_ENI=${web_eni:-UNKNOWN}"
echo "SOURCE_DEST_CHECK=${source_dest:-UNKNOWN}"
echo "PRIVATE_ROUTE_TABLE=${EXPECTED_PRIVATE_ROUTE_TABLE}"
echo "PRIVATE_DEFAULT_ROUTE_STATE=${route_state:-NONE}"
if [ -n "${route_nat:-}" ] && [ "$route_nat" != "None" ]; then
  echo "PRIVATE_DEFAULT_ROUTE_TARGET=${route_nat}"
elif [ -n "${route_eni:-}" ] && [ "$route_eni" != "None" ]; then
  echo "PRIVATE_DEFAULT_ROUTE_TARGET=${route_eni}"
else
  echo "PRIVATE_DEFAULT_ROUTE_TARGET=NONE"
fi
echo "NAT_GATEWAY_EXPECTED_ID=${EXPECTED_NAT_GATEWAY_ID}"
echo "NAT_EIP=${eip_public:-UNKNOWN}"
echo "NAT_EIP_ALLOCATION_ID=${eip_alloc:-UNKNOWN}"
echo "NAT_EIP_ASSOCIATION=${eip_assoc:-NONE}"

if [ "${eip_alloc:-}" = "$EXPECTED_NAT_EIP_ALLOCATION_ID" ] \
  && [ "${eip_public:-}" = "$EXPECTED_NAT_EIP_PUBLIC_IP" ]; then
  if [ -z "${eip_assoc:-}" ] || [ "${eip_assoc:-}" = "None" ]; then
    echo "NAT_EIP_STATE=ALLOCATED_UNASSOCIATED"
    echo "NAT_EIP_DEPENDENCY=RESERVED_NOT_REQUIRED_FOR_WEB_NAT_INSTANCE"
  else
    echo "NAT_EIP_STATE=ALLOCATED_ASSOCIATED"
    echo "NAT_EIP_DEPENDENCY=ASSOCIATED_RESOURCE_PRESENT"
  fi
else
  echo "NAT_EIP_STATE=CONTRACT_MISMATCH"
  echo "NAT_EIP_DEPENDENCY=CONTRACT_MISMATCH"
fi

nat_state="NOT_FOUND"
nat_subnet="NONE"
nat_allocation="NONE"
nat_public="NONE"
while IFS=$'\t' read -r nat_id state subnet allocation public_ip; do
  [ -n "${nat_id:-}" ] || continue
  if [ "$nat_id" = "$EXPECTED_NAT_GATEWAY_ID" ]; then
    nat_state="$state"
    nat_subnet="$subnet"
    nat_allocation="$allocation"
    nat_public="$public_ip"
  fi
done <<< "$nat_records"
echo "NAT_GATEWAY_STATE=${nat_state}"
echo "NAT_GATEWAY_SUBNET=${nat_subnet}"
echo "NAT_GATEWAY_PUBLIC_IP=${nat_public}"
echo "NAT_GATEWAY_ALLOCATION_ID=${nat_allocation}"

if [ "$LOCAL_CHECK" -eq 1 ]; then
  ip_forward="$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo UNKNOWN)"
  firewall_backend="$(iptables --version 2>/dev/null || echo IPTABLES_UNAVAILABLE)"
  iptables -S "$FORWARD_CHAIN" >/dev/null 2>&1 && forward_rule=PRESENT || forward_rule=ABSENT
  iptables -t nat -S "$POSTROUTING_CHAIN" >/dev/null 2>&1 && nat_rule=PRESENT || nat_rule=ABSENT
  ip link show "$EXPECTED_WEB_NAT_INTERFACE" >/dev/null 2>&1 && wg_interface=UP_OR_PRESENT || wg_interface=ABSENT
  wg_route="$(ip route show 10.251.0.0/24 2>/dev/null | tr '\n' ' ' || true)"
  echo "IP_FORWARD=${ip_forward}"
  echo "WEB_FIREWALL_BACKEND=${firewall_backend}"
  echo "NAT_RULE_PRESENT=${nat_rule}"
  echo "FORWARD_RULE_PRESENT=${forward_rule}"
  echo "WIREGUARD_INTERFACE=${wg_interface}"
  echo "WIREGUARD_ROUTE=${wg_route:-NONE}"
else
  echo "IP_FORWARD=NOT_CHECKED_CONTROLLER_ONLY"
  echo "WEB_FIREWALL_BACKEND=NOT_CHECKED_CONTROLLER_ONLY"
  echo "NAT_RULE_PRESENT=NOT_CHECKED_CONTROLLER_ONLY"
  echo "FORWARD_RULE_PRESENT=NOT_CHECKED_CONTROLLER_ONLY"
  echo "WIREGUARD_INTERFACE=NOT_CHECKED_CONTROLLER_ONLY"
  echo "WIREGUARD_ROUTE=NOT_CHECKED_CONTROLLER_ONLY"
fi

web_identity_ok=1
if [ "${web_private:-}" != "$EXPECTED_WEB_PRIVATE_IP" ] \
  || [ "${web_public:-}" != "$EXPECTED_WEB_PUBLIC_IP" ] \
  || [ "${web_eni:-}" != "$EXPECTED_WEB_ENI" ] \
  || [ "${source_dest:-}" != "False" ]; then
  web_identity_ok=0
fi

blackhole_route=0
if [ "${route_state:-}" = "blackhole" ] \
  && [ "${route_nat:-}" = "$EXPECTED_NAT_GATEWAY_ID" ]; then
  blackhole_route=1
fi

if [ "$web_identity_ok" -eq 1 ] \
  && [ "${route_eni:-}" = "$EXPECTED_WEB_ENI" ] \
  && [ "${route_state:-}" = "active" ]; then
  current_mode=WEB_NAT_INSTANCE
  current_status=READY
elif [ "${route_nat:-}" = "$EXPECTED_NAT_GATEWAY_ID" ] \
  && [ "${route_state:-}" = "active" ] \
  && [ "$nat_state" = "available" ]; then
  current_mode=NAT_GATEWAY
  current_status=NOT_CONFIGURED
elif [ "$blackhole_route" -eq 1 ]; then
  current_mode=BLACKHOLE
  if [ "$nat_state" = "NOT_FOUND" ] && [ "$web_identity_ok" -eq 1 ] \
    && [ "${web_state:-}" = "running" ] \
    && [ "${eip_alloc:-}" = "$EXPECTED_NAT_EIP_ALLOCATION_ID" ] \
    && [ "${eip_public:-}" = "$EXPECTED_NAT_EIP_PUBLIC_IP" ]; then
    current_status=READY_FOR_BLACKHOLE_RECOVERY
  elif [ "$web_identity_ok" -eq 0 ]; then
    current_status=DRIFT
  else
    current_status=NOT_READY
  fi
elif [ "${route_state:-}" = "blackhole" ] || [ "${route_state:-}" = "None" ] \
  || [ -z "${route_state:-}" ]; then
  current_mode=BLACKHOLE
  current_status=NOT_READY
else
  current_mode=UNKNOWN
  current_status=DRIFT
fi

echo "CURRENT_EGRESS_MODE=${current_mode}"
echo "NAT_INSTANCE_STATUS=${current_status}"

case "$current_status" in
  READY|NOT_CONFIGURED|READY_FOR_BLACKHOLE_RECOVERY) exit 0 ;;
  NOT_READY|DRIFT) exit 1 ;;
  *) exit 2 ;;
esac
