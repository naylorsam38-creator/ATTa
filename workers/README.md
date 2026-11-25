# workers

Background workers (ingestion, sandboxed code execution, browser automation, research). Workers receive
permissions only from the task that dispatches them and can never grant permissions (policy rule, Stage 01).
Resource limits come from `workers.*` in configuration. First worker lands in Stage 06.
