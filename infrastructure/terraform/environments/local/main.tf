terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "= 3.6.2"
    }
  }
}

provider "docker" {}

variable "database_password" {
  type      = string
  sensitive = true
}

variable "postgres_superuser_password" {
  type      = string
  sensitive = true
}

module "platform" {
  source                      = "../../modules/platform_host"
  database_password           = var.database_password
  postgres_superuser_password = var.postgres_superuser_password
  bootstrap_sql_path          = abspath("${path.module}/../../../../database/bootstrap.sql")
}
