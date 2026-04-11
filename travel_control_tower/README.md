# Travel Control Tower

This directory contains the first implementation scaffold for the travel planning product.

Current focus:

- fix the MVP scope
- define stable input/output contracts
- build a maintainable planner core first
- add the web layer after the planning flow is stable

Why start this way:

- the planning engine is the actual product moat
- the UI can change later, but unstable contracts will slow every iteration
- export features for Excel, Word, and PPT already exist in the workspace as scripts, so the next stable step is to standardize the planning data model

Current structure:

- `docs/`: product-facing and engineering-facing notes
- `contracts/`: JSON schemas for request and result objects
- `examples/`: sample inputs and outputs
- `planner_core/`: pure Python domain layer and orchestration stub

Immediate next milestones:

1. freeze request/response shape
2. add route provider adapters
3. add hotel/transport candidate selection
4. expose planner core through an API
5. build the first web result page
