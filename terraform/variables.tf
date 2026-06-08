variable "project" {
  description = "Project name"
  type        = string
  default     = "agent-orchestrator"
}

variable "environment" {
  description = "Environment (production, staging)"
  type        = string
  default     = "production"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidr" {
  description = "Public subnet CIDR block"
  type        = string
  default     = "10.0.1.0/24"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size" {
  # WARNING: only ever INCREASE this. EBS cannot shrink in place, so lowering
  # it forces a root-volume REPLACEMENT on `terraform apply` — a blank disk
  # that destroys Postgres/Grafana/Prometheus/certs/job-archive state. The
  # 100 GB is headroom for multi-stage build spikes (15-30 GB transient), not
  # steady-state use (~12 GB). To go smaller, do a snapshot + manual volume
  # swap in a maintenance window — see docs/phase0-cost-report.md.
  description = "Root EBS volume size in GB (increase-only; see warning)"
  type        = number
  default     = 100
}

variable "ssh_allowed_cidrs" {
  description = "CIDR blocks allowed to SSH into EC2 (your IP/32)"
  type        = list(string)
}

variable "ssh_public_key" {
  description = "SSH public key content for EC2 access"
  type        = string
}
