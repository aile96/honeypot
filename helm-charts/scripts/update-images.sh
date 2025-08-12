#!/bin/bash
set -euo pipefail

REGISTRY="${REGISTRY}"
USERNAME="${REGISTRY_USERNAME}"
PASSWORD="${REGISTRY_PASSWORD}"

echo "Getting all deployments..."

deployments=$(kubectl get deployments --all-namespaces -o json)

echo "$deployments" | jq -c '.items[]' | while read -r deployment; do
    ns=$(echo "$deployment" | jq -r '.metadata.namespace')
    name=$(echo "$deployment" | jq -r '.metadata.name')

    containers=$(echo "$deployment" | jq -c '.spec.template.spec.containers[]')

    echo "$containers" | while read -r container; do
        image=$(echo "$container" | jq -r '.image')

        if [[ "$image" == $REGISTRY* ]]; then
            imagename=$(basename "$image" | cut -d: -f1)
            current_tag=$(basename "$image" | cut -d: -f2)

            echo "Checking tags for $imagename..."

            echo "PORCO DIO $USERNAME:$PASSWORD:$REGISTRY"

            tags=$(curl -s -u "$USERNAME:$PASSWORD" -k \
                "https://$REGISTRY/v2/$imagename/tags/list" | jq -r '.tags[]')

            echo "PORCO DIO 1"

            latest_tag=$(echo "$tags" | sort -V | tail -n 1)

            echo "PORCO DIO 2"

            if [[ "$latest_tag" != "$current_tag" ]]; then
                echo "Updating $name in $ns to $latest_tag..."
                kubectl -n "$ns" set image deployment/"$name" \
                  "$imagename=$REGISTRY/$imagename:$latest_tag"
            fi
        fi
    done
done
