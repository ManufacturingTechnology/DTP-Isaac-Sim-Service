---
title: 'Overview'
---

## asdf

## sdfg

Development overview


## Repository Structure

| Path | Name | Description |
|---|---|---|
| `coprocessor/` | [Isaac Sim Co-Processor (ISCP)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/coprocessor) | A stand-alone application which extends Isaac Sim simulation capabilities. |
| `container/` | [Isaac Sim Container (ISDD)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/container) | A docker-compose container deployment of Isaac Sim with the [ISE](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension). |
| `docs/` | - | A static website containing documentation for this module. |
| `extension/` | [Isaac Sim Extension (ISE)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) | An extension loaded by Isaac Sim which extends native capabilities. |
| `lib/` | [Isaac Sim Library (ISL)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/lib) | A python library enabling external applications to interact with the [ISE](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension). |

## Include Heirarchy

``` mermaid
---
title: Project Heirarchy
---
flowchart TD
    ISC["fa:fa-cloud Container (ISC)"] --> ISE["fa:fa-industry Extension (ISE)"]
    ISE --> ISD["fa:fa-book Docs (ISD)"]
    ISE --> ISL["fa:fa-box Library (ISL)"]
    ISE --> ISCP["fa:fa-gear Co-Processor (ISCP)"]
    ISCP --> ISCPL["fa:fa-box Library (ISL)"]
```



## Develop

To develop or debug the [Isaac Sim Extension (ISE)](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service/tree/main/extension) itself, use the following instructions.

### Linux

1. Clone this repository
1. Run `make dev` in the `extension` directory.
1. Start Isaac Sim, open the Extensions Manager (`window > extensions`), open settings (`hamburger > settings`), and add
a search path (`Extension Search Paths > Scroll to Bottom > Green Plus Sign`), and open/specify the `extension\exts`
directory in this repository.
1. Open the Extensions Manager (`window > extensions`), open the `Third Party` tab, search for `amt.dtp.ise`, enable the `amt.dtp.ise` extension, and enable autoload.
1. [Optional]: A 3Dconnexion SpaceMouse works without extra installs; on Linux, add the udev rule from the Install page's SpaceMouse section.

### Windows

1. Clone this repository
1. Run `dev.bat` in the `extension` directory.
1. Start Isaac Sim, open the Extensions Manager (`window > extensions`), open settings (`hamburger > settings`), and add
a search path (`Extension Search Paths > Scroll to Bottom > Green Plus Sign`), and open/specify the `extension\exts`
directory in this repository.
1. Open the Extensions Manager (`window > extensions`), open the `Third Party` tab, search for `amt.dtp.ise`, enable the `amt.dtp.ise` extension, and enable autoload.
1. [Optional]: A 3Dconnexion SpaceMouse works without extra installs (see the Install page's SpaceMouse section).

