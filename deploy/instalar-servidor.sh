#!/bin/bash
# Prepara uma máquina Ubuntu Server 24.04 LTS para rodar o CompraFácil.
# Use como "Dados do usuário" (User data) ao criar a instância EC2:
# roda uma vez, como root, quando a máquina liga pela primeira vez.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Docker, plugin "docker compose" e git
apt-get update
apt-get install -y docker.io docker-compose-v2 git
systemctl enable --now docker
usermod -aG docker ubuntu

# 2 GB de swap: dá folga para o build e para o PostgreSQL em máquina pequena
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
fi
