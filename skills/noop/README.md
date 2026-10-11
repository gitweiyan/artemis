# noop — measure the reflex path, do nothing else

```bash
artemis reflex probe --skill noop --seconds 10
```

Reports frames/s, capture latency p50/p95 and blind frames for the device. Use it to
answer "can this device run a reflex loop at all?" before blaming a game skill, and
after a device starts misbehaving (it isolates capture from perception and policy).

Nothing here touches the a11y tree, the database or the model.
