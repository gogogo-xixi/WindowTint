# WindowTint UI Design

## Reference

Adapted from the local `awesome-design-md` Raycast specification and inverted
into a light desktop-tool surface.

WindowTint keeps its own identity and workflow. This document borrows the
surface ladder, hairline borders, restrained radius language, monochrome
hierarchy, and single accent treatment rather than copying Raycast branding.

## Principles

1. The product window is the focal point; application chrome stays quiet.
2. Hierarchy comes from surface luminance and typography, not shadows.
3. Borders are one physical pixel and use a low-contrast hairline.
4. Primary actions are near-black; secondary actions are white and outlined.
5. Chromatic color is reserved for focus, links, live state, and warnings.
6. Controls remain dense enough for repeated desktop use.

## Tokens

| Role | Value |
|---|---|
| Canvas | `#f4f6f8` |
| Surface | `#eef2f6` |
| Elevated surface | `#ffffff` |
| Card surface | `#f8fafc` |
| Primary ink | `#101114` |
| Body ink | `#30343a` |
| Muted ink | `#6b7280` |
| Hairline | `#dfe3e8` |
| Accent | `#2563eb` |
| Accent soft | `#eaf1ff` |
| Warning | `#b45309` |
| Warning soft | `#fff7ed` |

## Components

- Primary button: near-black fill, white label, 6-8px visual radius.
- Secondary button: white fill, hairline border, dark label.
- Buttons, sliders, switches, selects, and segmented choices are drawn as
  rounded application controls rather than platform-default rectangles.
- Rounded geometry is rendered through 4x supersampling and Lanczos downscaling
  so corners remain smooth at desktop scale.
- Panels: elevated surface with a 1px hairline border.
- Tabs: muted unselected labels; selected tab uses elevated surface and primary ink.
- Inputs: elevated surface, hairline border, muted placeholder, light entered text.
- Scrollbars follow Clash Verge Rev: 8px transparent track, rounded
  `#8c8c8c` thumb, and no arrow buttons. Reference:
  `clash-verge-rev/clash-verge-rev:src/assets/styles/index.scss`.
- Status text: muted by default; accent blue for success and warning yellow for errors.
- Preview: cool-gray frame around a bright product-window mockup.

## Layout

- Outer canvas gutters: 30px.
- Panel gutters: 22-28px.
- Control groups: numbered sections separated by full-width hairlines.
- Footer actions: persistent, separated from scrolling controls.
- Minimum supported window: 980x680.

## Avoid

- Large rounded pill containers for ordinary desktop commands.
- Visible platform-default checkboxes, radio buttons, or slider thumbs.
- Multiple competing accent colors.
- Drop shadows or decorative gradients on application chrome.
- Light cards floating inside a light canvas; use the surface ladder instead.
