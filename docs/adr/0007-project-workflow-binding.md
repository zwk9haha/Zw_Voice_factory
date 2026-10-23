# One current workflow binding per Voice Project

The project UI binds to exactly one published workflow at a time. A binding stores the workflow identity and published version; project runs are created through that binding and inherit the project ID in their input context. Editing a workflow creates a draft, and publishing a later version does not alter existing runs or silently switch the project.

## Decision

- The Workflow Canvas owns draft editing, validation, and publication.
- The Voice Project owns one explicit Project Workflow Binding.
- The Project Workflow Surface renders inputs from the bound published definition and renders run history and outputs.
- Switching the binding is an explicit project action.

## Consequences

- A project has a predictable run surface and does not need to understand canvas layout.
- Workflows remain reusable across projects.
- A future release can add named workflow roles or multiple bindings without changing the run-version rule.
