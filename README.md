# Via Tools

Automatically generate and manage via stitching patterns within copper zones and pads. This plugin stores stitching parameters for each zone and pad, allowing you to seamlessly update and regenerate stitched areas as your board layout evolves.

This plugin uses the new KiCad API via [kicad-python](https://gitlab.com/kicad/code/kicad-python) and requires KiCad 10 or later.

![screenshot](resources/screenshot.png)

## Installation

- Download the packaged version of this plugin: [via-tools.zip](https://nightly.link/narottamroyal/via-tools/workflows/package/main/via-tools.zip)
- Open KiCad's Plugin and Content Manager
- Click `Install from File...` and select `via-tools.zip` to install the plugin
- A dialog box may appear asking if you would like to enable the KiCad API. Select `Yes` to enable the KiCad API

## Known Issues

- Rule areas with `Keep Out Vias` are not respected
