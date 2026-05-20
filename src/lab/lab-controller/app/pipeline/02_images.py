#!/usr/bin/env python3
"""Build and push all lab images into the shared cache registry."""

from lib import (
    build_cache_image,
    cache_image_ref,
    config_int,
    load_image_definitions,
    log,
    parallel_map,
    push_cache_image,
    set_state_value,
)


def main() -> None:
    definitions = load_image_definitions(CONFIG)
    selected = definitions
    build_workers = config_int(CONFIG, "IMAGE_BUILD_PARALLELISM", 4, minimum=1)
    push_workers = config_int(CONFIG, "IMAGE_PUSH_PARALLELISM", 4, minimum=1)

    log(f"Building/checking {len(selected)} cache image(s) with parallelism={build_workers}.")
    built_refs = parallel_map("image build", build_workers, selected, lambda image: build_cache_image(CONFIG, image))

    log(f"Pushing {len(built_refs)} cache image(s) with parallelism={push_workers}.")
    refs_to_push = [ref for ref in built_refs if ref]
    pushed_refs = parallel_map("image push", push_workers, refs_to_push, lambda ref: push_cache_image(CONFIG, ref))

    set_state_value(STATE, "image_definitions", [image.get("name") for image in definitions])
    set_state_value(STATE, "cache_images", [cache_image_ref(CONFIG, image) for image in selected])
    set_state_value(STATE, "built_cache_images", refs_to_push)
    set_state_value(STATE, "pushed_cache_images", [ref for ref in pushed_refs if ref])
    log("Image build/cache step completed.")


if __name__ == "__main__":
    main()
