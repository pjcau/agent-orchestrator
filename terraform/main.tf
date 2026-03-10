# ============================================================
# Agent Orchestrator — AWS Infrastructure
# ============================================================
# Prerequisites:
#   1. Bootstrap the backend first:
#      cd terraform/backend && terraform init && terraform apply
#   2. Generate SSH key:
#      ssh-keygen -t ed25519 -f ~/.ssh/agent-orchestrator -N ""
#   3. Copy terraform.tfvars.example to terraform.tfvars and fill in values
#   4. terraform init && terraform plan && terraform apply
# ============================================================

terraform {
  required_version = ">= 1.5"

  backend "s3" {
    bucket         = "agent-orchestrator-tfstate"
    key            = "infra/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "agent-orchestrator-tflock"
    encrypt        = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# --- Networking ---
module "networking" {
  source = "./modules/networking"

  project            = var.project
  vpc_cidr           = var.vpc_cidr
  public_subnet_cidr = var.public_subnet_cidr
  availability_zone  = "${var.aws_region}a"
  ssh_allowed_cidrs  = var.ssh_allowed_cidrs
}

# --- IAM ---
module "iam" {
  source = "./modules/iam"

  project = var.project
}

# --- EC2 ---
module "ec2" {
  source = "./modules/ec2"

  project              = var.project
  instance_type        = var.instance_type
  subnet_id            = module.networking.public_subnet_id
  security_group_id    = module.networking.security_group_id
  iam_instance_profile = module.iam.instance_profile_name
  ssh_public_key       = var.ssh_public_key
  root_volume_size     = var.root_volume_size
}
