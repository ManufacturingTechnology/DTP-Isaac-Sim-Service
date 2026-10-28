---
title: 'Workpiece Lifecycle Demo'
---

# GUI

1. Start Isaac Sim with the `AMT DTP Isaac Sim Extension (ISE)` enabled. See the [Install](020-Install.md) page for instructions.

1. Download the cup scene, [`main.usd`](https://raw.githubusercontent.com/ManufacturingTechnology/DTP-Isaac-Sim-Service-Alpha-Testbeds/main/Demos/cup-1/main.usd) (`Demos/cup-1/` in the [DTP-Isaac-Sim-Service-Alpha-Testbeds](https://github.com/ManufacturingTechnology/DTP-Isaac-Sim-Service-Alpha-Testbeds) repository).

1. Drag and drop `main.usd` onto the Isaac stage to open it, or `File > Open` and navigate to `main.usd`.

1. Open the `Debug` window from the menu bar, `AMT > Debug` (it is hidden when the extension starts), and expand its
`Workpiece Lifecycle Demo` section.

1. Press `Play` to start the timeline/simulation.
In the `Debug` window, use the `Spawn` commands to generate a workpiece, and the `First Workpiece` commands manipulate the first available workpiece in order of when they were spawned.
Note that changing a workpiece to a different phase/state deletes the existing workpiece and spawns a new workpiece, which affects which workpiece will be manipulated by the next `First Workpiece` command.
When finished, press the `Stop` button to stop the timeline/simulation, which will reset the simulation. 

    ???+ info

        If an error occurs, stopping then starting the timeline/simulation may resolve the issue

    ???+ tip

        Use `Shift + Left Mouse Click and Drag` to move the workpiece and/or cup while playing.

    ???+ warning

        Converting fluids/particles into rigids/deformables is under development and is not robust, especially when
        the fluid is allowed to flatten out on a flat surface, which makes a rigid/deformable mesh difficult to
        generate. Use the cup to capture fluids/particles before converting them to rigids/deformables.

    ???+ tip

        When converting fluids/particles in the cup into a rigid, it may be in contact with the cup and get stuck
        in the cup. In stead, convert it to a deformable which allows it to fall out of the cup, then convert it 
        to a rigid.


