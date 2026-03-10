#!/usr/bin/env bash
# ============================================================
# EC2 First-Time Setup Script
# Sets up the monitoring dashboard at monitoring.agents-orchestrator.com
# ============================================================
# Usage:
#   scp scripts/setup-ec2.sh ec2-user@<EC2_IP>:~/
#   ssh ec2-user@<EC2_IP>
#   chmod +x setup-ec2.sh && ./setup-ec2.sh
# ============================================================

set -euo pipefail

DOMAIN="monitoring.agents-orchestrator.com"
APP_DIR="/opt/agent-orchestrator"

echo "=== EC2 Setup for $DOMAIN ==="

# --- 1. Install Docker & Docker Compose ---
echo "[1/6] Installing Docker..."
if ! command -v docker &>/dev/null; then
    sudo yum update -y
    sudo yum install -y docker git
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker ec2-user
    echo "Docker installed. You may need to re-login for group changes."
fi

if ! docker compose version &>/dev/null; then
    echo "Installing Docker Compose plugin..."
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep tag_name | cut -d '"' -f4)
    sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

echo "Docker: $(docker --version)"
echo "Compose: $(docker compose version)"

# --- 2. Create app directory ---
echo "[2/6] Setting up app directory..."
sudo mkdir -p "$APP_DIR"
sudo chown "$(whoami):$(whoami)" "$APP_DIR"

# --- 3. Create .env.prod ---
echo "[3/6] Configuring environment..."
ENV_FILE="$APP_DIR/.env.prod"
if [ ! -f "$ENV_FILE" ]; then
    JWT_SECRET=$(openssl rand -hex 32)
    PG_PASSWORD=$(openssl rand -hex 16)
    cat > "$ENV_FILE" <<EOF
# === Production Environment ===
ENVIRONMENT=production
BASE_URL=https://${DOMAIN}

# --- PostgreSQL ---
POSTGRES_PASSWORD=${PG_PASSWORD}

# --- Auth (fill these in) ---
JWT_SECRET_KEY=${JWT_SECRET}
OAUTH_CLIENT_ID=
OAUTH_CLIENT_SECRET=

# --- API Keys ---
OPENROUTER_API_KEY=

# --- Grafana ---
GRAFANA_PASSWORD=$(openssl rand -hex 12)

# --- GitHub ---
GITHUB_USERNAME=
EOF
    echo "Created $ENV_FILE — EDIT IT to fill in OAuth and API keys:"
    echo "  nano $ENV_FILE"
else
    echo "$ENV_FILE already exists, skipping."
fi

# --- 4. Open firewall ports ---
echo "[4/6] Checking firewall..."
echo "Make sure your EC2 Security Group allows:"
echo "  - TCP 80  (HTTP, for Let's Encrypt)"
echo "  - TCP 443 (HTTPS)"
echo "  - TCP 22  (SSH, your IP only)"

# --- 5. SSL certificate ---
echo "[5/6] SSL certificate setup..."
echo ""
echo "After DNS propagation (A record: ${DOMAIN} -> this IP), run:"
echo ""
echo "  cd $APP_DIR"
echo "  docker compose -f docker-compose.prod.yml up -d nginx"
echo "  docker compose -f docker-compose.prod.yml run --rm certbot certonly \\"
echo "    --webroot -w /var/www/certbot \\"
echo "    -d ${DOMAIN} \\"
echo "    --agree-tos --email YOUR_EMAIL --non-interactive"
echo "  docker compose -f docker-compose.prod.yml restart nginx"
echo ""

# --- 6. Start services ---
echo "[6/6] Ready to start!"
echo ""
echo "After editing .env.prod and obtaining SSL cert:"
echo ""
echo "  cd $APP_DIR"
echo "  docker compose -f docker-compose.prod.yml --env-file .env.prod up -d"
echo ""
echo "Health check: curl -sk https://${DOMAIN}/health"
echo ""
echo "=== Setup complete ==="
