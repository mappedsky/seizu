#!/bin/bash

mkdir -p ./.compose/telegraf ./.compose/elasticmq ./.compose/neo4j ./.compose/seizu ./.compose/dynamodb ./.compose/authentik/blueprints \
  ./.compose/cartography/analysis \
  ./.compose/cartography/reports/aibom \
  ./.compose/cartography/reports/docker-scout \
  ./.compose/cartography/reports/semgrep \
  ./.compose/cartography/reports/syft \
  ./.compose/cartography/reports/trivy

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
