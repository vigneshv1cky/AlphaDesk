variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-east4"
}

variable "zone" {
  description = "GCP zone"
  type        = string
  default     = "us-east4-a"
}

variable "admin_username" {
  description = "Dashboard Basic Auth username"
  type        = string
  sensitive   = true
}

variable "admin_password" {
  description = "Dashboard Basic Auth password"
  type        = string
  sensitive   = true
}

variable "alpaca_key" {
  description = "Alpaca API key"
  type        = string
  sensitive   = true
}

variable "alpaca_secret" {
  description = "Alpaca secret key"
  type        = string
  sensitive   = true
}

variable "polygon_key" {
  description = "Polygon.io API key"
  type        = string
  sensitive   = true
}

variable "ds_api_key" {
  description = "DeepSeek API key"
  type        = string
  sensitive   = true
}
