terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "= 3.6.2"
    }
  }
}

variable "database_password" {
  type        = string
  sensitive   = true
  description = "Password for the atta application role (from configuration/local.env, never committed)."
}

variable "postgres_superuser_password" {
  type      = string
  sensitive = true
}

variable "bootstrap_sql_path" {
  type        = string
  description = "Absolute path to database/bootstrap.sql."
}

resource "docker_network" "atta" {
  name = "atta"
}

resource "docker_volume" "pgdata" {
  name = "atta-pgdata"
}

resource "docker_image" "postgres" {
  name = "pgvector/pgvector:0.8.1-pg16"
}

resource "docker_image" "redis" {
  name = "redis:7.4-alpine"
}

resource "docker_container" "postgres" {
  name    = "atta-postgres"
  image   = docker_image.postgres.image_id
  restart = "unless-stopped"
  env = [
    "POSTGRES_USER=postgres",
    "POSTGRES_PASSWORD=${var.postgres_superuser_password}",
  ]
  ports {
    internal = 5432
    external = 5432
    ip       = "127.0.0.1"
  }
  volumes {
    volume_name    = docker_volume.pgdata.name
    container_path = "/var/lib/postgresql/data"
  }
  volumes {
    host_path      = var.bootstrap_sql_path
    container_path = "/atta/bootstrap.sql"
    read_only      = true
  }
  networks_advanced {
    name = docker_network.atta.name
  }
}

resource "docker_container" "redis" {
  name    = "atta-redis"
  image   = docker_image.redis.image_id
  restart = "unless-stopped"
  command = ["redis-server", "--save", "", "--appendonly", "no"]
  ports {
    internal = 6379
    external = 6380
    ip       = "127.0.0.1"
  }
  networks_advanced {
    name = docker_network.atta.name
  }
}

output "postgres_endpoint" {
  value = "127.0.0.1:5432"
}

output "redis_endpoint" {
  value = "127.0.0.1:6380"
}
