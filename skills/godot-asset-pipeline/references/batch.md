# Automatic asset batch

Read this when turning an identified set of subjects into an unattended production run. The project backend must be version 0.2.0 or newer. `snapshot.project` identifies the actual connected Godot project.

## One brief, one run

Call `asset_pipeline_create_batch` with `{"spec": brief}`. Creation builds the complete graph without submitting paid requests. `asset_pipeline_plan_batch` previews reused work and remaining stages. `asset_pipeline_run_batch` starts the batch once; the backend advances stages and imports outputs without more agent calls.

```json
{
  "id": "street_core_v1",
  "label": "Street core assets",
  "reference_node_id": "concept_reference",
  "concurrency": 2,
  "assets": [
    {
      "id": "arcade",
      "name": "Arcade storefront",
      "prompts": {
        "subject": "Extract only the arcade storefront from image[1]. Preserve its red sign and awning. Complete the shell, remove people and street ground. One isolated subject, uniform neutral background.",
        "front": "Exact front elevation of the same storefront in image[1], full silhouette, same proportions and fixtures, neutral background. One view only.",
        "right": "Image[1] is the subject and image[2] its front view. Show the same building's right elevation, front at image left; preserve dimensions and fixtures.",
        "back": "Image[1] is the subject and image[2] its front view. Show the same building from behind. Infer a restrained closed rear wall; do not repeat the front sign face."
      },
      "image_params": {"model": "seedream_v5"},
      "model_params": {
        "model": "P2-20260801", "quad": false,
        "face_limit": 16000, "texture": true, "pbr": true,
        "texture_quality": "standard"
      }
    }
  ]
}
```

Prompts describe the user's identified subjects; do not reuse the example storefront for an unrelated scene. The backend creates subject, front, right, back and model stages for every asset. The default ordered inputs are subject ← reference, front ← subject, right/back ← subject then front, model ← front/right/back with explicit view roles. The model endpoint gets image inputs, not a freeform prompt. Image parameters must be provider-supported fields; do not send `view` as an image API parameter.

## Optional shared image references

When the user wants an additional image to inform every subject and view, declare it once at the top level of the brief:

```json
{
  "reference_node_id": "concept_reference",
  "image_reference_inputs": [
    {"node_id": "accepted_video_frame", "role": "layout_reference"}
  ]
}
```

This fragment extends the complete brief above. The referenced node must already exist or be imported by that brief's `references`. The original concept remains the appearance input; an accepted concept-video frame can supply layout, orientation, and relative-proportion context. These are **actual ordered image dependencies** used in image-generation requests, not metadata-only links or assembly-only notes.

`image_reference_inputs` is optional. Omitting it or supplying `[]` keeps the previous behavior. It is an ordered array of objects with `node_id` and optional `role` (default `reference`). The backend validates node IDs and requires each node to exist; supplied roles must be nonempty and at most 100 characters. It appends the shared edges after each newly created image stage's own resolved inputs:

| Stage | Default image order with shared references |
| --- | --- |
| subject | Original concept (or `subject_reference_node_ids` in their order), then shared references |
| front | Generated subject, then shared references |
| right / back | Generated subject, generated front, then shared references |
| model | Isolated front, right, back with their explicit view roles; no shared references appended |

For an explicit `stage_inputs` override, its resolved order comes first and shared references follow. Duplicate `node_id` values are removed, preserving the first occurrence's position and role; repeated shared entries are still validated. Do not assume a reference is the last image when it was already present among a stage's own inputs. Inspect the planned ordered dependencies when numbering references in prompts.

For a video layout frame, explain its purpose in each image-stage prompt while preserving subject/front identity. Require a single isolated asset or the requested single elevation; exclude neighboring buildings, map annotations, dimension lines, and video overlays from the output. The shared whole-scene frame does not become a `front`, `right`, or `back` input at the 3D endpoint. This optional workflow does not change the unattended batch: identify subjects and fill prompts once, let the backend build and run the chain, and review only after generation completes.

`existing_node_ids` preserves adopted nodes' original recipes and versions; shared references are not retroactively appended to those nodes. If a new dependency is deliberately added to a live recipe, old outputs retain their actual recorded upstream versions and become stale as appropriate. Do not reimport them as though the new image had been used. For `existing_outputs`, declare shared references only if they really participated in the historical generation; otherwise adopt the original nodes or keep that import in a separate brief. Creating or changing these dependencies does not start paid regeneration.

## Existing work and exact dependencies

- Optional top-level `references` imports previously produced support images in the same creation operation. Each entry has `id`, `label`, `path`, and optional `kind`, `prompt`, `inputs`, `metadata`. Entries can reference existing nodes or earlier entries. Use this for a historical extraction draft that was the real input to a later cleanup; these imported references are not newly generated stages.
- `subject_reference_node_ids` lists actual reference nodes in order, replacing the single default concept reference. This can include a separate style asset.
- `stage_inputs` overrides the stage-specific ordered dependencies before optional shared image references are appended. Use it when imported historical work used a different reference order. Each edge is `{"stage":"subject","role":"reference"}` for another stage in the same asset or `{"node_id":"existing_id","role":"reference"}` for an existing node. Do not invent a front-to-right dependency if the right image was generated using only the subject.
- `existing_outputs` maps a stage to a local file path or `{"path":"...","role":"image","metadata":{"provider":"..."}}`. Use this to import previously produced images directly, without regenerating them. The source and exact prompt must describe the real generation.
- `existing_node_ids` maps stage names to existing nodes. Their original recipes and versions are preserved; repeated IDs are scheduled only once. Adopt saved model tasks this way rather than submitting them again.
- Give the batch and assets stable IDs. Repeating an identical brief must reuse the recorded batch; a changed brief requires a deliberate new batch ID.

## Progress and recovery

`snapshot.batches` contains batch records with `id`, `label`, `status`, `node_ids`, per-asset stage mappings, `progress.completed/total`, `completed_nodes`, `total_nodes`, `active_node_ids`, `blocked_node_ids`, and `error`. Per-node job records hold provider progress and saved task IDs. Godot polls this shared state and updates existing cards, including externally started batches.

`batch.pause` stops local scheduling/polling; remote work can continue and be billed. `batch.resume` continues the recorded work using saved task IDs. A failed or ambiguous submission is not automatically submitted again. Changed recipes or upstream versions must not silently replace the batch's frozen inputs. Report the specific blocked stage, preserve history, and use targeted repair only within the user's scope.

Wait for the batch to finish before visual review and scene placement. Read-only progress monitoring is allowed; do not insert an approval, visual acceptance, or manual next-step gate between normal stages.

The optional `tools/run_batch.py` client submits this same brief and watches status; it contains no image generation logic. All actual image/model requests, polling and downloads belong to the persistent backend.
