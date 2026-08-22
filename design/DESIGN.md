---
name: Synthetic Integrity
colors:
  surface: '#12131a'
  surface-dim: '#12131a'
  surface-bright: '#393841'
  surface-container-lowest: '#0d0e15'
  surface-container-low: '#1b1b23'
  surface-container: '#1f1f27'
  surface-container-high: '#292932'
  surface-container-highest: '#34343d'
  on-surface: '#e3e1ec'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#e3e1ec'
  inverse-on-surface: '#303038'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#c0c1ff'
  on-secondary: '#292a60'
  secondary-container: '#3f4178'
  on-secondary-container: '#aeb0ee'
  tertiary: '#ffaaf7'
  on-tertiary: '#5a005d'
  tertiary-container: '#d664d3'
  on-tertiary-container: '#4f0052'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#e1e0ff'
  secondary-fixed-dim: '#c0c1ff'
  on-secondary-fixed: '#13144a'
  on-secondary-fixed-variant: '#3f4178'
  tertiary-fixed: '#ffd6f7'
  tertiary-fixed-dim: '#ffaaf7'
  on-tertiary-fixed: '#37003a'
  on-tertiary-fixed-variant: '#7e0a81'
  background: '#12131a'
  on-background: '#e3e1ec'
  surface-variant: '#34343d'
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Geist
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
  body-lg:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-mono:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  headline-lg-mobile:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  gutter: 24px
  margin: 32px
  container-max: 1440px
  stack-sm: 8px
  stack-md: 16px
  stack-lg: 32px
---

## Brand & Style

This design system is engineered for technical precision, reliability, and high-performance monitoring. It targets engineers and developers building autonomous agents, where the emotional response must be one of absolute control and systematic clarity.

The visual style is **High-Tech Minimalism with Glassmorphic accents**. It utilizes a "dark-first" architecture to reduce eye strain during deep-work sessions. The interface feels like a sophisticated terminal—refined through subtle depth, structured grids, and a high-contrast functional hierarchy that surfaces critical failure points instantly.

## Colors

The palette is rooted in a deep-space foundation. The primary background is derived from a muted neutral base, providing a stable canvas for technical data.

- **Electric Indigo (#6366F1):** The primary signature color, used for critical actions, active states, and highlighting AI "thought" processes.
- **Muted Lavender (#7072AC):** The secondary color, used for supporting UI elements, secondary navigation, and less urgent data visualizations.
- **Cyber Magenta (#B84AB7):** The tertiary color, reserved for specialized high-fidelity signals, experimental features, or distinct data categories.
- **Semantic Logic:** Status colors are used at full saturation for alerts, but should be used as 10% opacity tints for background washes in log entries to maintain a professional, non-distracting environment.

## Typography

The design system utilizes **Geist** for its systematic, utilitarian aesthetic and exceptional legibility in dense interfaces. **JetBrains Mono** is introduced as a secondary label font for monospaced data, timestamps, and terminal outputs, reinforcing the platform's developer-centric nature.

Hierarchy is maintained through weight rather than just size. Headlines use a semi-bold weight with tight tracking to feel "locked in," while body text remains regular for maximum readability in log streams.

## Layout & Spacing

This design system employs a **Structured Fluid Grid**. The layout is built on an 8px base unit to ensure perfect alignment of data-heavy modules.

- **Desktop (1440px+):** 12-column grid with 24px gutters. Sidebars are fixed at 280px to maintain consistent navigation.
- **Tablet (768px - 1439px):** 8-column grid with 16px gutters.
- **Mobile (<767px):** 4-column grid with 16px margins. Information density is reduced, hiding secondary metadata in collapsible accordions.

Content modules (cards) should use `stack-md` (16px) for internal padding to maintain a sense of openness even when data density is high.

## Elevation & Depth

Hierarchy is achieved through **Tonal Layering** and **Glassmorphism**, rather than heavy shadows.

- **Base Layer:** The deepest surface, providing the primary foundation.
- **Intermediate Layer:** Uses a subtle tonal shift with a 1px border to create a clear visual hierarchy for primary content containers.
- **Floating Layer:** Surfaces use a semi-transparent fill with a 20px backdrop-blur. This is reserved for modals, dropdowns, and hover-state tooltips.
- **Interactive Depth:** On hover, interactive elements increase their border brightness rather than changing background color, mimicking the feel of a physical lit-up console.

## Shapes

To maintain a professional and technical tone, this design system uses **Soft (0.25rem)** roundedness. 

- **Buttons & Inputs:** 4px (0.25rem) radius.
- **Cards & Modules:** 8px (0.5rem) radius for a slightly softer container feel.
- **Data Points:** 2px or sharp corners for graph elements and terminal blocks to emphasize precision.

## Components

### Buttons
Primary buttons use a solid `Electric Indigo` fill with high-contrast text. Secondary buttons use a ghost style: 1px border with a subtle hover transition to a `Muted Lavender` border.

### Chips & Badges
Status indicators use a "dot and label" format. A small circular indicator (8px) of the status color sits next to a Mono-font label. For "AI Running" states, the dot should have a soft pulse animation.

### Cards
Cards are the primary container. They feature a 1px border derived from the neutral-variant. Header sections within cards are separated by a subtle horizontal rule of the same border color.

### Input Fields
Fields use a dark-filled background to contrast against the surface containers. Focus states should trigger a 1px `Electric Indigo` border and a subtle outer glow.

### Reliability Logs
A specialized component for this system. It uses a zebra-stripe pattern where alternate rows have a subtle opacity increase. Monospaced text is mandatory for the "Agent Response" column to ensure alignment of code snippets.