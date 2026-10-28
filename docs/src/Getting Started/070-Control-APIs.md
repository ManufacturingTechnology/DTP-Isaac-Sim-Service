---
title: 'Control APIs'
---

# Control APIs

Machines and robots in a testbed can be controlled remotely: each asset groups its sensors and actuators
into an **automation component** (`AutomationComponent`), and a **segment** API (`SegmentApi`) serves one or
more components over HTTP.
- Clients queue frames of commands, and one frame is applied per physics step; a frame that fails halts
  the queue until the client resumes or clears it.
- Clients stream each step's state, with sequence numbers, and can resume a stream after a disconnect.
- A typed Python client module is generated for each machine.

The details are in **Asset Development**:

| To… | Read |
|---|---|
| understand components and how they are built | [Automation Components](../Asset Development/040-Automation-Components.md) |
| control a machine from Python or any HTTP client | [Segments and the Control APIs](../Asset Development/050-Segments-and-Control-APIs.md) |
| look up validation rules, Swagger examples, fingerprints, and the demo assets | [Segment API Reference](../Asset Development/050-Segments-and-Control-APIs.md#segment-api-reference) |
| see a complete example (the AMT VM-A1 mill) | [Machines](../Asset Development/060-Machines.md) |
| understand the design | [Segments and the Control APIs: Layers](../Asset Development/050-Segments-and-Control-APIs.md#layers) |
