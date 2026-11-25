# infrastructure/terraform

Stage 01 provides a **local, zero-cost** single-host module (`modules/platform_host`) using the Docker provider:
PostgreSQL 16 + pgvector and Redis containers bound to 127.0.0.1, the same topology as
`infrastructure/docker/compose.yaml`. Cloud modules are added only with explicit authorisation for paid
infrastructure (`security.allow_paid_infrastructure`) — none exist yet.

    cd infrastructure/terraform/environments/local
    terraform init && terraform plan -var-file=local.tfvars     # local.tfvars is git-ignored

Provider versions are pinned in `.terraform.lock.hcl` after the first `terraform init` on an operator machine.
Stage 01 status: `terraform fmt -check` passes; `terraform validate` is **BLOCKED** in the build environment
(registry.terraform.io unreachable for provider download) — see BLOCKERS.md B-004.
