# Interface design notes

The interface uses original HTML, CSS, and JavaScript, with no external runtime,
fonts, CDN assets, or React build step. Research references accessed on 21 October 2026:

- [21st dashboard collection](https://21st.dev/community/components/explore/dashboard-component): cohesive navigation, metric cards, filters, and empty states.
- [21st spotlight guide](https://docs.21st.dev/blog/react-spotlight-effect-components): a light radial gradient driven by CSS custom properties.
- [Aceternity Card Spotlight](https://ui.aceternity.com/components/card-spotlight): restrained highlights on dark panels.
- [React Bits Spotlight Card](https://reactbits.dev/components/spotlight-card): pointer interaction as a visual enhancement.

These are design references; their component source was not copied or installed.

## Landing page

The system introduction is `/`. It explains the architecture, reliability
concepts, an operations walkthrough, and limitations. Interactive component buttons
explain the actual request path and independent monitoring path. Context buttons
switch between system overview and recovery workflow explanations. These are content
views, not authenticated roles. The live status comes from the real summary API.

The hero diagram is explicitly labelled an architecture illustration. Its moving
orbit points are decorative, not measurements of traffic. Replica indicator colours
come from real monitoring status. Cursor movement subtly shifts the centre emblem.

## Dashboard

The operational dashboard is `/dashboard`. It includes dark/light theme switching,
focus mode, pause/resume of UI refresh (the worker keeps recording), a real
request tester, per-instance inspection, target and window selectors, event filters,
and chart inspection with a pointer or keyboard arrow keys. The demo guide gives
copyable terminal commands and never controls Docker through the browser.

Live backend measurements and browser request-test latency have separate labels.
Historical failures stay in the totals after recovery. Monitoring outages make the
UI visibly stale; no missing samples are filled with synthetic data.

Pointer effects are disabled for coarse pointers and reduced-motion preferences.
All critical actions have buttons/links and keyboard focus styles. Native dialogs
trap focus and support Escape. No custom cursor replaces the normal system pointer.
