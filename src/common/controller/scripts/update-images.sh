#!/bin/bash
set -euo pipefail

: "${REGISTRY:?Missing REGISTRY}"
: "${REGISTRY_USERNAME:?Missing REGISTRY_USERNAME}"
: "${REGISTRY_PASSWORD:?Missing REGISTRY_PASSWORD}"

USERNAME="$REGISTRY_USERNAME"
PASSWORD="$REGISTRY_PASSWORD"

# Set to true to preview actions without updating deployments
DRY_RUN="${DRY_RUN:-false}"

# Only consider version-like tags:
# 1
# 1.2
# 1.2.3
# v1.2.3
VERSION_REGEX='^v?[0-9]+(\.[0-9]+)*$'

is_greater_version() {
    local current="$1"
    local candidate="$2"

    [[ -n "$current" ]] || return 1
    [[ -n "$candidate" ]] || return 1
    [[ "$current" != "$candidate" ]] || return 1

    local highest
    highest=$(printf "%s\n%s\n" "$current" "$candidate" | sort -V | tail -n 1)

    [[ "$highest" == "$candidate" ]]
}

echo "Getting all deployments..."

kubectl get deployments --all-namespaces -o json |
jq -c '.items[]' |
while read -r deployment; do
    ns=$(echo "$deployment" | jq -r '.metadata.namespace')
    name=$(echo "$deployment" | jq -r '.metadata.name')

    echo "$deployment" | jq -c '.spec.template.spec.containers[]' |
    while read -r container; do
        container_name=$(echo "$container" | jq -r '.name')
        image=$(echo "$container" | jq -r '.image')

        # Remove digest if the image is pinned by SHA
        image_no_digest="${image%@*}"

        # Process only images from the configured registry
        if [[ "$image_no_digest" != "$REGISTRY/"* ]]; then
            continue
        fi

        last_segment="${image_no_digest##*/}"

        # Extract the current tag and image repository
        if [[ "$last_segment" == *:* ]]; then
            current_tag="${last_segment##*:}"
            image_repo="${image_no_digest%:*}"
        else
            echo "WARN: skipping $ns/$name/$container_name because the image has no tag: $image"
            continue
        fi

        # Skip non-version tags such as latest, dev, main, staging, etc.
        if ! [[ "$current_tag" =~ $VERSION_REGEX ]]; then
            echo "WARN: skipping $ns/$name/$container_name because the current tag is not a version: $current_tag"
            continue
        fi

        # Extract repository path inside the registry
        repo_path="${image_repo#${REGISTRY}/}"

        if [[ -z "$repo_path" || "$repo_path" == "$image_repo" ]]; then
            echo "WARN: skipping $ns/$name/$container_name due to unexpected image format: $image"
            continue
        fi

        echo "Checking $ns/$name/$container_name"
        echo "Current image: $image"
        echo "Repository: $repo_path"
        echo "Current tag: $current_tag"

        # Fetch all tags from the Docker Registry HTTP API v2
        tags=$(
            curl -fsSk -u "$USERNAME:$PASSWORD" \
                "https://$REGISTRY/v2/$repo_path/tags/list" |
            jq -r '.tags[]?'
        ) || {
            echo "WARN: failed to fetch tags for $repo_path"
            continue
        }

        # Keep only version-like tags and select the highest one
        latest_tag=$(
            echo "$tags" |
            grep -E "$VERSION_REGEX" |
            sort -V |
            tail -n 1
        ) || true

        if [[ -z "$latest_tag" ]]; then
            echo "WARN: no version-like tags found for $repo_path"
            continue
        fi

        echo "Latest available tag: $latest_tag"

        # Update only if the latest available tag is greater than the current tag
        if is_greater_version "$current_tag" "$latest_tag"; then
            new_image="$image_repo:$latest_tag"

            echo "Updating $ns/$name/$container_name"
            echo "  From: $image"
            echo "  To:   $new_image"

            if [[ "$DRY_RUN" == "true" ]]; then
                echo "DRY_RUN=true, skipping update"
            else
                kubectl -n "$ns" set image deployment/"$name" \
                    "$container_name=$new_image"

                kubectl -n "$ns" rollout status deployment/"$name"
            fi
        else
            echo "No update needed for $ns/$name/$container_name"
        fi

        echo "---"
    done
done