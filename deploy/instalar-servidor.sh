#!/bin/bash
# Prepara uma máquina Amazon Linux 2023 para rodar o CompraFácil.
# Use como "User data" ao criar a instância EC2 (roda uma vez, como root).
set -euo pipefail

# Docker e git
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user

# Plugin "docker compose" (o pacote do Amazon Linux não traz)
COMPOSE_VERSAO=v2.29.7
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSAO}/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 2 GB de swap: dá folga para o build e para o PostgreSQL em máquina pequena
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
fi
