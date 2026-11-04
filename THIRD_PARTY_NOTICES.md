# Third-party component sources

- `web/src/components/reactbits/SpotlightCard.tsx` is vendored from [React Bits](https://reactbits.dev/components/spotlight-card), [upstream source](https://github.com/DavidHDev/react-bits/blob/main/src/ts-tailwind/Components/SpotlightCard/SpotlightCard.tsx). The local copy is formatted and tracked in this repository; upstream terms are retained in `web/vendor/react-bits.LICENSE`.
- `web/src/components/ui` follows the source-owned component composition used by [shadcn/ui](https://ui.shadcn.com/) and uses Radix primitives, class-variance-authority, clsx and tailwind-merge. These local wrappers are maintained with this application.
- Other dependency versions are pinned by `uv.lock`, `requirements.lock` and `web/package-lock.json`; individual package licenses remain with their distributions.
- Country SVG flags are loaded on demand from [flag-icons](https://github.com/lipis/flag-icons), MIT license; terms are retained in `web/vendor/flag-icons.LICENSE` and included in the runtime image.
