variable "project" {
  description = "Project name for resource tagging"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "subnet_id" {
  description = "Subnet ID to launch the instance in"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID for the instance"
  type        = string
}

variable "iam_instance_profile" {
  description = "IAM instance profile name"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key for the deploy key pair"
  type        = string
}

variable "root_volume_size" {
  # Increase-only: lowering this replaces the root volume (blank disk, data
  # loss). See the root variables.tf and docs/phase0-cost-report.md.
  description = "Root EBS volume size in GB (increase-only; see warning)"
  type        = number
  default     = 100
}
