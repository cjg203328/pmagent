# Artifact Generation Contract

This document is the current contract for natural-language file delivery. It
describes runtime behavior, not archived design intent.

## Supported outputs

| Format | Plan payload | File-level verification |
| --- | --- | --- |
| XLSX | worksheet name, columns and rows | reopen workbook and compare sheets and cells |
| DOCX | typed paragraphs | reopen document and compare paragraph text |
| PPTX | slide titles and bullet text | reopen presentation and verify every slide text |
| PDF | typed paragraphs | reopen PDF, verify page count and searchable text |

The `documents` dependency profile owns the format libraries. Imports for
PowerPoint and PDF generation stay inside capability methods so the core
Runtime does not import them during startup.

## Canonical flow

```text
chat/API/CLI
  -> canonical Harness run_turn
  -> artifact handler
  -> ArtifactCoordinator detects one explicit format and action
  -> deterministic plan for explicit simple content, otherwise strict LLM JSON
  -> WorkspaceArtifactGenerator writes a format-specific temporary file
  -> format verifier reopens and checks planned structure and content
  -> versioned atomic publish and SHA-256 integrity comparison
  -> TurnResult artifact metadata
  -> UI download card
```

Once an explicit artifact action and format are detected, planning or
generation errors remain artifact errors. They must not fall through to normal
model chat, because a prose answer is not a substitute for the requested file.

## Safety and authority

- User input cannot provide storage paths. Filenames are normalized and all
  artifacts remain direct children of the workspace artifact root.
- Existing files are never overwritten. Name collisions create a new version.
- Delete, overwrite and in-place modification requests are rejected by this
  generation path.
- Failed verification leaves no published artifact.
- A successful artifact carries size, SHA-256, version and a `verification`
  object with `status: passed` and publication integrity evidence.
- Office/PDF files are delivery artifacts, not knowledge authority. Knowledge
  ingestion remains a separate WorkspaceKnowledgeStore operation.

## Planning boundary

Explicit simple requests such as a one-sentence document or a table with named
columns are generated without an LLM. Complex requests use a strict Pydantic
plan with bounded rows, columns, paragraphs, slides and text lengths. Ambiguous
multi-format prompts are rejected and ask the user to choose one format.

Visual inspection remains appropriate for high-design business deliverables.
The runtime verifier guarantees openability and content/structure agreement;
it does not claim that arbitrary model-planned layouts have received human
visual approval.
