# Project metadata and design lock

Place optional project-level intent in `deck.meta.json` beside the `.pptd`
manifest. Keeping it separate preserves PPTD v2 and local-editor compatibility.
Quality reports include this file in the source fingerprint when it exists.

Use the file to lock decisions that should survive page-by-page authoring:

```json
{
  "schemaVersion": 1,
  "audience": "Executive product and engineering leaders",
  "objective": "Choose the migration sequence",
  "coreMessage": "Start with the two reversible, high-leverage services",
  "primaryLanguage": "en-AU",
  "consumptionMode": "live",
  "pageRhythm": ["anchor", "dense", "dense", "breathing"],
  "targetApps": ["powerpoint", "libreoffice"],
  "mediaPolicy": {
    "licensePolicy": "known-only",
    "requireAttribution": true,
    "allowRemote": false,
    "allowGenerative": true
  },
  "transitions": {
    "default": {"type": "fade", "durationMs": 500, "advanceOnClick": true},
    "pages": {
      "pages/04-demo.page": {
        "type": "push",
        "direction": "left",
        "advanceAfterMs": 12000,
        "advanceOnClick": true
      }
    }
  }
}
```

The safe transition vocabulary is `none`, `fade`, `push`, `wipe`, and
`morph`. A backend may reject a member it cannot preserve; it must not silently
substitute another transition. The machine-readable contract is
`reference/schemas/deck-metadata.schema.json`.
