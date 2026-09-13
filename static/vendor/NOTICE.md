# Third-party assets

Bundled so the dashboard renders without internet access. Refresh with
`python scripts/vendor_assets.py`.

| File | Project | License |
|---|---|---|
| `tailwindcss.js` | [Tailwind CSS](https://tailwindcss.com) play CDN build, with the forms and container-queries plugins | MIT |
| `fonts/*.woff2` (Geist) | [Geist](https://vercel.com/font) by Vercel | SIL Open Font License 1.1 |
| `fonts/*.woff2` (JetBrains Mono) | [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | SIL Open Font License 1.1 |
| `fonts/*.woff2` (Material Symbols) | [Material Symbols](https://fonts.google.com/icons) by Google | Apache License 2.0 |

`fonts.css` is the Google Fonts stylesheet for these families, with font URLs
rewritten to the local files.
