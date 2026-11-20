# Review checklist

- Follow empty, malformed, duplicate, and boundary inputs through the changed path.
- Check authorization where data crosses a user, workspace, or process boundary.
- Check whether writes are atomic and failures leave recoverable state.
- Inspect asynchronous cancellation, cleanup, and concurrent updates when relevant.
- Compare public inputs, outputs, and persisted formats with existing callers.
- Verify tests exercise externally observable behavior and meaningful failure cases.
- For each finding, identify a reproducible trigger and its practical impact.
