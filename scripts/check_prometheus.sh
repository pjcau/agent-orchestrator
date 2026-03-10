#!/bin/bash
# Check Prometheus targets status on EC2
HOST="52.212.89.114"
KEY="$HOME/.ssh/agent-orchestrator"
ssh -i "$KEY" ec2-user@"$HOST" 'cd /opt/agent-orchestrator && docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T prometheus sh -c "wget -qO- http://localhost:9090/api/v1/targets"'
