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
        container_name=$(echo "$container" | jq -r '.name')
        image=$(echo "$container" | jq -r '.image')

        if [[ "$image" == $REGISTRY* ]]; then
            image_no_digest="${image%@*}"
            last_segment="${image_no_digest##*/}"
            if [[ "$last_segment" == *:* ]]; then
                current_tag="${last_segment##*:}"
                image_repo="${image_no_digest%:*}"
            else
                current_tag=""
                image_repo="$image_no_digest"
            fi

            repo_path="${image_repo#${REGISTRY}/}"
            if [[ -z "$repo_path" || "$repo_path" == "$image_repo" ]]; then
                echo "WARN: skipping $name/$container_name due to unexpected image format: $image"
                continue
            fi

            echo "Checking tags for $repo_path..."

            tags=$(curl -s -u "$USERNAME:$PASSWORD" -k \
                "https://$REGISTRY/v2/$repo_path/tags/list" | jq -r '.tags[]?')

            latest_tag=$(echo "$tags" | sort -V | tail -n 1)

            if [[ -n "$latest_tag" && "$latest_tag" != "$current_tag" ]]; then
                echo "Updating $name/$container_name in $ns to $latest_tag..."
                kubectl -n "$ns" set image deployment/"$name" \
                  "$container_name=$image_repo:$latest_tag" \
                  || { echo "WARN: update failed for $name/$container_name"; continue; }
            fi
        fi
    done
done
