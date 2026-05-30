#!/usr/bin/env bash
ce_run "ip addr" ip addr
ce_run "ip route" ip route
ce_run "ss -tunlp" ss -tunlp
ce_run "iptables -L" iptables -L -n
ce_run "cat /etc/resolv.conf" cat /etc/resolv.conf
ce_run "ip neigh" ip neigh
