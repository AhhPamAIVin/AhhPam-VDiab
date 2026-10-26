"""Meal Intelligence steps (Epic 1).

Each step is a plain async function over typed inputs/outputs, so the same
code serves the REST endpoints today and the multi-purpose patient agent
(as a tool or a graph node) later — no step reads HTTP or chat state.

- recognize_from_photo: photo -> draft item list (the photo is not kept).
"""
