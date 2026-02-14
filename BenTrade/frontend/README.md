# BenTrade Web App (No-framework, multi-dashboard structure)

## Run
Open `index.html` in your browser.

## Structure
- `index.html` = App shell (header + left nav + iframe router)
- `dashboards/credit-spread.html` = Your existing dashboard
- `assets/css/app.css` = Shared theme/styles
- `assets/js/app.js` = Shared dashboard logic (used by credit-spread.html)

## Add a new dashboard
1. Copy `dashboards/credit-spread.html` to `dashboards/<new>.html`
2. Update the title and any unique markup
3. Add the route in `index.html` (routes map)
4. Add a nav link with `data-route="<new>"`


## SPA routing (no iframe)
- `index.html` loads `dashboards/*.view.html` into `#view` via fetch.
- Add routes in `assets/js/router.js`.
