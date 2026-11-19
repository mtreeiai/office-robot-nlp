// Commit the generated assets: Python deployments do not require Node or a CDN.
const fs = require('node:fs');
const path = require('node:path');
const destination = path.join(__dirname, 'vendor');
fs.mkdirSync(destination, { recursive: true });
for (const [source, target] of [['index.global.min.js', 'fullcalendar.min.js'], ['LICENSE.md', 'fullcalendar-LICENSE.md']]) {
  fs.copyFileSync(path.join(__dirname, 'node_modules/fullcalendar', source), path.join(destination, target));
}
fs.copyFileSync(path.join(__dirname, 'node_modules/@fullcalendar/core/locales/vi.global.min.js'), path.join(destination, 'fullcalendar-vi.min.js'));
fs.copyFileSync(path.join(__dirname, 'node_modules/preact/LICENSE'), path.join(destination, 'preact-LICENSE.txt'));
