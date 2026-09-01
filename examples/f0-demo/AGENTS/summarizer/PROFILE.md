---
name: summarizer
version: 1.0.0
description: Deterministic F0 summarization worker.
function: transform
handles:
  - summarize
refuses:
  - translate
inputs: []
outputs:
  - text artifact
guarantees:
  - read-only inputs
  - writes only to destination
model_capacity: minimum
model_modality: text
skills:
  - summarize-text
harness: fake
---

# Mission

Produce one verified deterministic artifact for the F0 acceptance journey.
