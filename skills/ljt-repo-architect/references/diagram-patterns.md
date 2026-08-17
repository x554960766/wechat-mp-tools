# Diagram Patterns — Mermaid Templates

## Purpose

Standard Mermaid diagram templates for Phase 4. All diagrams must
render in GitHub-flavored Markdown preview.

## General Rules

1. Use only `flowchart`, `sequenceDiagram`, and `graph` types.
2. Node IDs: use `UPPER_SNAKE_CASE`. Node labels: use "Title Case".
3. Subgraphs for logical grouping; match module names from Phase 3.
4. Add a brief caption below each diagram.

## System-Context Diagram

```mermaid
flowchart LR
    subgraph EXTERNAL
        EXT_A["External Service A"]
        EXT_B["External Service B"]
    end
    REPO["This Repository"]
    REPO <--> EXT_A
    REPO --> EXT_B
```

Caption: System context showing external dependencies.

## Component Diagram

```mermaid
flowchart TD
    subgraph MODULE_A["Module A"]
        A1["Component A1"]
        A2["Component A2"]
    end
    subgraph MODULE_B["Module B"]
        B1["Component B1"]
    end
    A1 --> A2
    A2 --> B1
```

Caption: Internal component relationships.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant C as Client
    participant E as Entry Point
    participant M as Module
    participant D as Database
    C->>E: HTTP request
    E->>M: process(data)
    M->>D: query(sql)
    D-->>M: rows
    M-->>E: result
    E-->>C: response
```

Caption: Typical request flow.

## Validation

- No raw HTML in diagrams.
- No `classDef` unless required for clarity.
- Every node must have a label.
- Test by pasting into GitHub Markdown preview.
