# Contributing

1. Install dependencies and follow [setup](docs/SETUP.md).
2. Keep document reading, extraction, validation and model opinions separated.
3. Preserve source IDs, coordinate systems and original evidence when editing
   extraction behavior. Never manufacture OCR confidence for native PDF text.
4. Add meaningful regression coverage for behavior changes, then run the
   [test suite](docs/TESTING.md). Record skipped prerequisites honestly.
5. Update user/setup/architecture documentation when behavior changes.
6. Inspect staged files and publish only fictional fixtures. Never commit real
   invoices, screenshots containing private data, saved runs, model weights,
   credentials or virtual environments.

Use descriptive commits that state the resulting behavior. Keep generated
outputs local. A pull request should explain the problem, change and validation,
including remaining limitations. Do not present heuristic scores as measured
accuracy or model explanations as verified facts.

This repository has no selected license yet. Do not assume a grant to
redistribute the project or bundled dependencies beyond applicable permissions.
