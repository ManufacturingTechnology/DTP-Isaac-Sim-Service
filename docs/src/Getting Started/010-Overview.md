---
title: 'Overview'
---

[NVIDIA Isaac Sim](https://developer.nvidia.com/isaac/sim) is a software package built
on [NVIDIA Omniverse](https://developer.nvidia.com/omniverse) and is geared toward 
simulation, testing, and synthetic data generation in virtual environments for robotics 
applications. While Isaac Sim excels at these tasks for robotics applications, it does
not provide key capabilities required for many manufacturing applications. This service
aims to extend Isaac Sim's capabilities to better support manufacturing use cases.

In Omniverse termnology, an `Extension` is a plugin which is loaded by Isaac Sim at 
runtime and extends the capabilities of Isaac Sim. The `AMT DTP Isaac Sim Service (ISS)`
consists of multiple components packaged into the `AMT DTP Isaac Sim Extension (ISE)` 
which is loaded then executed by Isaac Sim. The following section, 
[Installation](020-Install.md), will guide you through the process of installing
Isaac Sim and the ISE.

In software development, a `Service-Oriented Architecture (SOA)` is an architectrual style
and development method that builds applications using discrete, reusable, and loosely
coupled components called `Services`. The `AMT Digital Testbed Platform (DTP)` is a
collection of services which can be modularly composed to construct digital manufacturing
testbeds using the SOA approach. This service, the `AMT DTP Isaac Sim Service (ISS)`, is
intended to be a foundational layer of the DTP SOA which provides factory simulation and
visualization capability for manufacturing testbeds.

Building on this foundational layer, multiple reusable services will be built to enable
modular testbeds construction, enabling entire manufacturing business to be modeled:

``` mermaid
flowchart LR
    subgraph B1[Business 1]
    ISS1((Isaac Sim Service))
    CRM1[CRM] <--> ISS1
    ERP1[ERP] <--> ISS1
    PDM1[PDM] <--> ISS1
    SCM1[SCM] <--> ISS1
    MES1[MES] <--> ISS1
    WMS1[WMS] <--> ISS1
    CMMS1[CMMS] <--> ISS1
    AI1[AI] <--> ISS1
    DT1[Digital Twins] <--> ISS1
    end
```

By combining and reusing these services to construct multiple testbeds, a manufacturing
supply network model can be realized:


``` mermaid
flowchart TD
    subgraph SN[Supply Network]
    B1(Business 1)
    B2(Business 2)
    B3(Business 3)
    B4(Business 4)
    B5(Business 5)
    end
    B1 <--> B2
    B1 <--> B3
    B1 <--> B4
    B1 <--> B5
    B2 <--> B3
    B2 <--> B4
    B2 <--> B5
    B3 <--> B4
    B3 <--> B5
    B4 <--> B5
```
