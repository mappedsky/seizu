#!/bin/bash

mkdir -p ./.compose/telegraf ./.compose/elasticmq ./.compose/neo4j ./.compose/seizu ./.compose/dynamodb ./.compose/authentik/blueprints \
  ./.compose/cartography/analysis \
  ./.compose/cartography/reports/aibom \
  ./.compose/cartography/reports/docker-scout \
  ./.compose/cartography/reports/semgrep \
  ./.compose/cartography/reports/syft \
  ./.compose/cartography/reports/trivy

copy_config_if_missing() {
  source_path="$1"
  destination_path="$2"

  # Docker Compose creates a directory when the source of a short-syntax bind
  # mount is absent. Repair that specific (empty-directory) state, but never
  # remove a non-empty directory or overwrite an operator-managed file.
  if [ -d "$destination_path" ]
  then
    if ! rmdir "$destination_path"
    then
      echo "Cannot initialize $destination_path: it is a non-empty directory." >&2
      return 1
    fi
  fi

  if [ ! -e "$destination_path" ]
  then
    cp "$source_path" "$destination_path"
  elif [ ! -f "$destination_path" ]
  then
    echo "Cannot initialize $destination_path: expected a regular file." >&2
    return 1
  fi
}

if [ ! -f ./.env ]
then
  cp ./.env.example ./.env
fi

if [ ! -f ./.compose/telegraf/telegraf.conf ]
then
  cp ./.config/dev/telegraf/telegraf.conf ./.compose/telegraf/telegraf.conf
fi

if [ ! -f ./.compose/elasticmq/.elasticmq.conf ]
then
  cp ./.config/dev/elasticmq/.elasticmq.conf ./.compose/elasticmq/.elasticmq.conf
fi

if [ ! -f ./.compose/neo4j/neo4j.conf ]
then
  cp ./.config/dev/neo4j/neo4j.conf ./.compose/neo4j/neo4j.conf
fi

if [ ! -f ./.compose/seizu/reporting-dashboard.yaml ]
then
  cp ./.config/dev/seizu/reporting-dashboard.yaml ./.compose/seizu/reporting-dashboard.yaml
fi

if [ ! -f ./.compose/authentik/blueprints/seizu.yaml ]
then
  cp ./.config/dev/authentik/blueprints/seizu.yaml ./.compose/authentik/blueprints/seizu.yaml
fi

if [ ! -f ./.compose/dynamodb/Dockerfile ]
then
  cp ./.config/dev/dynamodb/Dockerfile ./.compose/dynamodb/Dockerfile
fi

for cartography_config in \
  aws-config \
  azure_permission_relationships.yaml \
  gcp-credentials.json \
  gcp_permission_relationships.yaml \
  kube.config \
  oci-config \
  permission_relationships.yaml
do
  copy_config_if_missing \
    "./.config/dev/cartography/$cartography_config" \
    "./.compose/cartography/$cartography_config"
done
